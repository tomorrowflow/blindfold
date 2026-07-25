/// Frozen-proxy discovery inside the bundle (issue #213, ADR-0039's `.app ⊃ frozen-proxy ⊃
/// ui_dist` layering): the PyInstaller onefile binary (`packaging/blindfold-proxy.spec`,
/// issue #184) is embedded in the bundle beside the menu bar app's own executable, so the
/// target Mac needs no Python, no `uv`, no Node. Falls back to a developer-mode path
/// (`uv run blindfold serve`) when no embedded binary is present -- a debug `swift build`/
/// `swift run` invocation, not the assembled `.app`. Pure path logic, no filesystem I/O of
/// its own -- `fileExists` is the caller's seam (real `FileManager` in the shell, a fixed
/// closure in tests).
public enum ProxyBinaryLocator {
    public static func locate(
        bundledExecutableDirectory: String,
        proxyHost: String,
        proxyPort: Int,
        fileExists: (String) -> Bool
    ) -> (exePath: String, args: [String]) {
        let serveArgs = ["serve", "--host", proxyHost, "--port", String(proxyPort)]
        let embeddedPath = bundledExecutableDirectory + "/blindfold-proxy"

        if fileExists(embeddedPath) {
            return (embeddedPath, serveArgs)
        }

        return ("/usr/bin/env", ["uv", "run", "blindfold"] + serveArgs)
    }
}
