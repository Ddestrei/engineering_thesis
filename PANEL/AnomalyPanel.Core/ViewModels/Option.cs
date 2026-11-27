namespace AnomalyPanel.Core.ViewModels;

/// <summary>Pozycja listy wyboru: wartość i polska etykieta.</summary>
public sealed record Option<T>(T Value, string Label)
{
    public override string ToString() => Label;
}

/// <summary>Usługa okna zapisu pliku (w WPF: SaveFileDialog; w testach: stała ścieżka).</summary>
public interface IFileDialogService
{
    /// <returns>Ścieżka wybrana przez użytkownika albo null (anulowano).</returns>
    string? SaveFile(string title, string filter, string defaultFileName);
}
