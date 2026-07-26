namespace Blindfold.Core;

/// <summary>
/// The supervisor log (issue #239, ADR-0046, mirroring macOS's <c>SupervisorLog.swift</c>): a
/// durable, size-bounded, scrubbed-by-construction record of the supervisor's own lifecycle
/// events -- spawn attempt, exit outcome, the already-scrubbed <see cref="StartupRefusalReason"/>,
/// and stop/quit. This is the seam <see cref="ProxySupervisor"/> appends allowlisted lines
/// through, mirroring <see cref="IProxyProcessLauncher"/>'s stub-in-tests pattern.
/// </summary>
/// <remarks>
/// Policy, pinned here so a later "improve logging" change can't silently regress it
/// (ADR-0046): every line is built from a fixed allowlist of fields (exe path, args, exit code,
/// the pre-scrubbed refusal reason) -- never the child's raw stdout/stderr, never an environment
/// <i>value</i> (a variable name is fine), never a Store key/token/API key, never an entity or
/// surrogate value. <see cref="ProxySupervisor"/> never has raw child output to hand this seam
/// in the first place: it only ever passes <see cref="StartupRefusalReason.Scrub"/>'s already-safe
/// output through.
/// </remarks>
public interface ISupervisorLogSink
{
    void Append(string line);
}

/// <summary>
/// Back-compat default for every <see cref="ProxySupervisor"/> construction site/test that
/// predates this issue -- discards every line. Production wiring (<c>Blindfold.Tray</c>) passes
/// a real <see cref="FileSupervisorLogSink"/> instead.
/// </summary>
public sealed class NullSupervisorLogSink : ISupervisorLogSink
{
    public void Append(string line)
    {
    }
}

/// <summary>
/// The real, size-bounded, file-backed <see cref="ISupervisorLogSink"/> (issue #239) -- takes
/// an already-resolved <paramref name="path"/>, never resolves the real per-user OS location
/// itself (that's <c>Program.cs</c>'s job, mirroring every other per-user path this tray already
/// computes at its own call sites).
/// </summary>
public sealed class FileSupervisorLogSink : ISupervisorLogSink
{
    private readonly string _path;
    private readonly int _maxBytes;
    private readonly Func<DateTimeOffset> _clock;
    private readonly object _lock = new();

    /// <summary>
    /// <paramref name="maxBytes"/> bounds the file's on-disk size (issue #239's AC:
    /// "size-bounded — asserted by a test, not by inspection") -- once exceeded, the oldest
    /// whole lines are dropped from the front rather than rotating to a second file, keeping
    /// this a single, always-at-this-path log the Open Logs row can point at unconditionally.
    /// </summary>
    public FileSupervisorLogSink(string path, int maxBytes = 256 * 1024, Func<DateTimeOffset>? clock = null)
    {
        _path = path;
        _maxBytes = maxBytes;
        _clock = clock ?? (() => DateTimeOffset.UtcNow);
    }

    public void Append(string line)
    {
        lock (_lock)
        {
            var timestamp = _clock().ToString("o");
            var entryBytes = System.Text.Encoding.UTF8.GetBytes($"{timestamp} {line}\n");

            var directory = Path.GetDirectoryName(_path);
            if (!string.IsNullOrEmpty(directory))
            {
                Directory.CreateDirectory(directory);
            }

            var existing = File.Exists(_path) ? File.ReadAllBytes(_path) : Array.Empty<byte>();
            var contents = new byte[existing.Length + entryBytes.Length];
            existing.CopyTo(contents, 0);
            entryBytes.CopyTo(contents, existing.Length);

            if (contents.Length > _maxBytes)
            {
                var overflow = contents.Length - _maxBytes;
                var newlineIndex = Array.IndexOf(contents, (byte)'\n', overflow);
                contents = newlineIndex >= 0
                    ? contents[(newlineIndex + 1)..]
                    : Array.Empty<byte>();
            }

            File.WriteAllBytes(_path, contents);
        }
    }
}
