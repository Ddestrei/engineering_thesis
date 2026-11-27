using AnomalyPanel.Core.ViewModels;
using Microsoft.Win32;

namespace AnomalyPanel;

public sealed class WpfFileDialogService : IFileDialogService
{
    public string? SaveFile(string title, string filter, string defaultFileName)
    {
        var dialog = new SaveFileDialog { Title = title, Filter = filter, FileName = defaultFileName, AddExtension = true };
        return dialog.ShowDialog() == true ? dialog.FileName : null;
    }
}
