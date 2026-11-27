using System.Diagnostics;
using System.Globalization;
using System.Windows;
using System.Windows.Markup;
using AnomalyPanel.Core.Data;
using AnomalyPanel.Core.ViewModels;

namespace AnomalyPanel;

/// <summary>Kompozycja aplikacji: ustawienia → repozytorium SQL → ViewModel → okno.</summary>
public partial class App : Application
{
    private readonly Stopwatch _startup = Stopwatch.StartNew();

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        var pl = CultureInfo.GetCultureInfo("pl-PL");
        CultureInfo.DefaultThreadCurrentCulture = pl;
        CultureInfo.DefaultThreadCurrentUICulture = pl;
        FrameworkElement.LanguageProperty.OverrideMetadata(typeof(FrameworkElement),
            new FrameworkPropertyMetadata(XmlLanguage.GetLanguage(pl.IetfLanguageTag)));

        var log = new PerformanceLog();
        var settings = PanelSettings.Load();
        var repository = new SqlAnomalyRepository(settings.ConnectionString, log);
        var vm = new MainViewModel(repository, log, new WpfFileDialogService());
        var window = new MainWindow { DataContext = vm };
        window.Loaded += async (_, _) =>
        {
            await vm.InitializeCommand.ExecuteAsync(null);
            log.Record("Panel: start aplikacji (do pierwszej kolejki)", _startup.Elapsed, vm.TotalAlerts);
        };
        DispatcherUnhandledException += (_, args) =>
        {
            MessageBox.Show(args.Exception.Message, "Błąd panelu", MessageBoxButton.OK, MessageBoxImage.Error);
            args.Handled = true;
        };
        MainWindow = window;
        window.Show();
    }
}
