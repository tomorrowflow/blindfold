import Foundation
import Testing
@testable import BlindfoldCore

/// The one-shot `.env` import (issue #226, ADR-0044): copies chosen `BLINDFOLD_*` keys
/// into the launch environment once, never re-read on later launches. `DotEnvParser`
/// is the pure-parsing half, Linux-testable like every other seam in this module.
struct DotEnvImportTests {
    @Test
    func parsesASimpleKeyValueLine() throws {
        let parsed = try DotEnvParser.parse("BLINDFOLD_L3_MODEL=llama3\n")
        #expect(parsed == ["BLINDFOLD_L3_MODEL": "llama3"])
    }

    @Test
    func skipsBlankLinesAndComments() throws {
        let parsed = try DotEnvParser.parse("""

        # a comment
        BLINDFOLD_L3_MODEL=llama3

        """)
        #expect(parsed == ["BLINDFOLD_L3_MODEL": "llama3"])
    }

    @Test
    func stripsALeadingExportKeyword() throws {
        let parsed = try DotEnvParser.parse("export BLINDFOLD_L3_MODEL=llama3\n")
        #expect(parsed == ["BLINDFOLD_L3_MODEL": "llama3"])
    }

    @Test
    func stripsMatchingSurroundingQuotesFromAValue() throws {
        let parsed = try DotEnvParser.parse(#"BLINDFOLD_L3_MODEL="llama3""#)
        #expect(parsed == ["BLINDFOLD_L3_MODEL": "llama3"])
    }

    @Test
    func aLineWithNoEqualsSignThrowsAMalformedLineError() {
        #expect(throws: DotEnvParseError.malformedLine(number: 1)) {
            try DotEnvParser.parse("this is not a key-value line")
        }
    }

    @Test
    func aKnownNonSecretKeyPlansToTheLaunchEnvironmentDestination() {
        let plan = DotEnvImport.plan(
            fileValues: ["BLINDFOLD_L3_MODEL": "llama3"],
            currentValues: [:]
        )
        #expect(plan.entries == [
            DotEnvImportPlan.Entry(key: "BLINDFOLD_L3_MODEL", newValue: "llama3", previousValue: nil, destination: .settings),
        ])
    }

    @Test
    func aKnownSecretKeyPlansToTheSecretDestination() {
        let plan = DotEnvImport.plan(
            fileValues: ["BLINDFOLD_L3_API_KEY": "sk-abc"],
            currentValues: [:]
        )
        #expect(plan.entries == [
            DotEnvImportPlan.Entry(key: "BLINDFOLD_L3_API_KEY", newValue: "sk-abc", previousValue: nil, destination: .secret),
        ])
    }

    @Test
    func anUnknownBlindfoldKeyIsReportedAndSkipped() {
        let plan = DotEnvImport.plan(
            fileValues: ["BLINDFOLD_MADE_UP_KEY": "whatever"],
            currentValues: [:]
        )
        #expect(plan.entries.isEmpty)
        #expect(plan.unknownKeys == ["BLINDFOLD_MADE_UP_KEY"])
    }

    @Test
    func aNonBlindfoldKeyIsIgnoredEntirely() {
        let plan = DotEnvImport.plan(
            fileValues: ["SOME_OTHER_TOOLS_VAR": "whatever"],
            currentValues: [:]
        )
        #expect(plan.entries.isEmpty)
        #expect(plan.unknownKeys.isEmpty)
    }

    @Test
    func aLegacyOllamaKeyIsFlaggedAndNotImported() {
        let plan = DotEnvImport.plan(
            fileValues: ["BLINDFOLD_OLLAMA_ADDR": "http://localhost:11434"],
            currentValues: [:]
        )
        #expect(plan.entries.isEmpty)
        #expect(plan.unknownKeys.isEmpty)
        #expect(plan.legacyKeys == ["BLINDFOLD_OLLAMA_ADDR"])
    }

    @Test
    func aDatabaseURLKeyIsSurfacedSeparatelyNotAsAnOrdinaryEntry() {
        let plan = DotEnvImport.plan(
            fileValues: ["BLINDFOLD_DATABASE_URL": "postgresql://db/blindfold"],
            currentValues: [:]
        )
        #expect(plan.entries.isEmpty)
        #expect(plan.unknownKeys.isEmpty)
        #expect(plan.legacyKeys.isEmpty)
        #expect(plan.databaseURLValue == "postgresql://db/blindfold")
    }

    @Test
    func applyWritesASettingsEntryIntoTheLaunchEnvironmentStore() {
        let suiteName = "test-\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suiteName) }
        let store = LaunchEnvironmentStore(suiteName: suiteName)
        let plan = DotEnvImportPlan(entries: [
            DotEnvImportPlan.Entry(key: "BLINDFOLD_L3_MODEL", newValue: "llama3", previousValue: nil, destination: .settings),
        ])

        DotEnvImport.apply(plan, importDatabaseURL: false, into: store, secretsStore: UserDefaultsSecretsStore(suiteName: suiteName))

        #expect(store.values() == ["BLINDFOLD_L3_MODEL": "llama3"])
    }

    @Test
    func applyWritesASecretEntryIntoTheSecretsStoreNeverTheLaunchEnvironmentStore() {
        let launchSuiteName = "test-\(UUID().uuidString)"
        let secretsSuiteName = "test-\(UUID().uuidString)"
        defer {
            UserDefaults().removePersistentDomain(forName: launchSuiteName)
            UserDefaults().removePersistentDomain(forName: secretsSuiteName)
        }
        let store = LaunchEnvironmentStore(suiteName: launchSuiteName)
        let secretsStore = UserDefaultsSecretsStore(suiteName: secretsSuiteName)
        let plan = DotEnvImportPlan(entries: [
            DotEnvImportPlan.Entry(key: "BLINDFOLD_L3_API_KEY", newValue: "sk-abc", previousValue: nil, destination: .secret),
        ])

        DotEnvImport.apply(plan, importDatabaseURL: false, into: store, secretsStore: secretsStore)

        #expect(secretsStore.value(for: "BLINDFOLD_L3_API_KEY") == "sk-abc")
        #expect(store.values().isEmpty)
    }

    @Test
    func applyOmitsTheDatabaseURLWhenNotExplicitlyConfirmed() {
        let suiteName = "test-\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suiteName) }
        let store = LaunchEnvironmentStore(suiteName: suiteName)
        let plan = DotEnvImportPlan(databaseURLValue: "postgresql://db/blindfold")

        DotEnvImport.apply(plan, importDatabaseURL: false, into: store, secretsStore: UserDefaultsSecretsStore(suiteName: suiteName))

        #expect(store.values().isEmpty)
    }

    @Test
    func applyWritesTheDatabaseURLWhenExplicitlyConfirmed() {
        let suiteName = "test-\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suiteName) }
        let store = LaunchEnvironmentStore(suiteName: suiteName)
        let plan = DotEnvImportPlan(databaseURLValue: "postgresql://db/blindfold")

        DotEnvImport.apply(plan, importDatabaseURL: true, into: store, secretsStore: UserDefaultsSecretsStore(suiteName: suiteName))

        #expect(store.values() == ["BLINDFOLD_DATABASE_URL": "postgresql://db/blindfold"])
    }

    @Test
    func readingAMissingFileThrowsUnreadableRatherThanCrashing() {
        let missingURL = URL(fileURLWithPath: "/nonexistent/\(UUID().uuidString)/.env")
        #expect(throws: DotEnvParseError.unreadable) {
            try DotEnvImport.readFileValues(contentsOf: missingURL)
        }
    }

    @Test
    func aMalformedFileFailsCleanlyLeavingAnExistingLaunchEnvironmentUntouched() throws {
        let suiteName = "test-\(UUID().uuidString)"
        defer { UserDefaults().removePersistentDomain(forName: suiteName) }
        let store = LaunchEnvironmentStore(suiteName: suiteName)
        store.setValue("llama3", for: "BLINDFOLD_L3_MODEL")

        let fileURL = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).env")
        try "not a key-value line".write(to: fileURL, atomically: true, encoding: .utf8)
        defer { try? FileManager.default.removeItem(at: fileURL) }

        #expect(throws: DotEnvParseError.self) {
            let fileValues = try DotEnvImport.readFileValues(contentsOf: fileURL)
            let plan = DotEnvImport.plan(fileValues: fileValues, currentValues: store.values())
            DotEnvImport.apply(plan, importDatabaseURL: false, into: store, secretsStore: UserDefaultsSecretsStore(suiteName: suiteName))
        }
        #expect(store.values() == ["BLINDFOLD_L3_MODEL": "llama3"])
    }

    @Test
    func anEntryOverwritingAHeldValueCarriesThePreviousValueForPreview() {
        let plan = DotEnvImport.plan(
            fileValues: ["BLINDFOLD_L3_MODEL": "mixtral"],
            currentValues: ["BLINDFOLD_L3_MODEL": "llama3"]
        )
        #expect(plan.entries == [
            DotEnvImportPlan.Entry(key: "BLINDFOLD_L3_MODEL", newValue: "mixtral", previousValue: "llama3", destination: .settings),
        ])
    }
}
