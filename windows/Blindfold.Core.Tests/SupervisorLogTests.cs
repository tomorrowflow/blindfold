using Blindfold.Core;
using Xunit;

namespace Blindfold.Core.Tests;

/// <summary>
/// The durable, scrubbed supervisor log (issue #239): "Open Logs" previously opened an empty
/// directory because no structured file-logging existed. Mirrors macOS's
/// <c>SupervisorLog.swift</c> -- the real file-backed sink takes an already-resolved path
/// (never resolves the per-user OS location itself), so it's testable here against
/// disposable temp-file paths exactly like the Swift core.
/// </summary>
public class SupervisorLogTests
{
    [Fact]
    public void NullSupervisorLogSinkDiscardsEveryLine()
    {
        var sink = new NullSupervisorLogSink();
        sink.Append("spawn: exe=blindfold-proxy.exe args=serve");
    }

    private static string TempLogPath() =>
        Path.Combine(Path.GetTempPath(), $"blindfold-supervisor-log-{Guid.NewGuid()}.log");

    [Fact]
    public void FileSupervisorLogSinkAppendsALineToARealFile()
    {
        var path = TempLogPath();
        try
        {
            var sink = new FileSupervisorLogSink(path);

            sink.Append("refused: root Transit token outside dev mode");

            var contents = File.ReadAllText(path);
            Assert.Contains("refused: root Transit token outside dev mode", contents);
        }
        finally
        {
            File.Delete(path);
        }
    }

    /// <summary>
    /// The log must not grow without limit (issue #239's AC: "asserted by a test, not by
    /// inspection") -- a tiny <c>maxBytes</c> plus many appends proves the file is truncated
    /// rather than left to grow unbounded, and that the oldest lines are what gets dropped.
    /// </summary>
    [Fact]
    public void FileSupervisorLogSinkTruncatesOldestLinesOnceOverTheSizeBound()
    {
        var path = TempLogPath();
        try
        {
            var sink = new FileSupervisorLogSink(path, maxBytes: 200);

            for (var i = 0; i < 50; i++)
            {
                sink.Append($"event number {i} padded to make the line longer than it looks");
            }

            var bytes = File.ReadAllBytes(path);
            Assert.True(bytes.Length <= 200);
            var contents = File.ReadAllText(path);
            Assert.DoesNotContain("event number 0 ", contents);
            Assert.Contains("event number 49", contents);
        }
        finally
        {
            File.Delete(path);
        }
    }
}
