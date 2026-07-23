using System.Drawing;
using Blindfold.Core;

namespace Blindfold.Tray;

/// <summary>
/// Renders <see cref="TrayIconState"/>'s three buckets to an actual tray <see cref="Icon"/>,
/// plus the ADR-0038 Unprotected alarm badge overlay -- drawn at runtime rather than shipped as
/// static assets, since the only thing that varies is a flat color and a small badge dot.
/// </summary>
internal static class TrayIcons
{
    private const int Size = 16;

    public static Icon For(TrayIconState state, bool showsAlarmBadge)
    {
        var color = state switch
        {
            TrayIconState.Protected => Color.SeaGreen,
            TrayIconState.Degraded => Color.Goldenrod,
            TrayIconState.StoppedOrRefused => Color.Gray,
            _ => throw new ArgumentOutOfRangeException(nameof(state), state, "unhandled TrayIconState"),
        };

        using var bitmap = new Bitmap(Size, Size);
        using (var g = Graphics.FromImage(bitmap))
        {
            g.Clear(Color.Transparent);
            using var brush = new SolidBrush(color);
            g.FillEllipse(brush, 1, 1, Size - 2, Size - 2);

            if (showsAlarmBadge)
            {
                using var alarmBrush = new SolidBrush(Color.Crimson);
                g.FillEllipse(alarmBrush, Size - 7, Size - 7, 6, 6);
            }
        }

        var handle = bitmap.GetHicon();
        return Icon.FromHandle(handle);
    }
}
