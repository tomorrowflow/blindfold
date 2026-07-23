using Xunit;
using Blindfold.Core;

/// <summary>
/// Tracer test: proves the C# project builds and <c>dotnet test</c> runs green inside the
/// Sandcastle Linux sandbox (ADR-0042). Grows into the real reducer / presentation /
/// loopback-guard tests — asserted against the shared golden vectors (ADR-0041) — as
/// issue #194 lands.
/// </summary>
public class BlindfoldCoreTests
{
    [Fact]
    public void PackageIdentifiesItself()
    {
        Assert.Equal("Blindfold.Core", BlindfoldCore.Name);
    }
}
