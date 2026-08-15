//
//  Command.swift
//
//  Created by Sunny Young.
//

import Foundation
import ArgumentParser

struct Command {
    enum Error: @unchecked Sendable, LocalizedError {
        case executing(command: String, error: NSDictionary)
        case invalidBinaryPath(String)

        var isPermissionDenied: Bool {
            guard case let .executing(_, error) = self else { return false }
            let message = error.description.lowercased()
            return message.contains("permission denied")
                || message.contains("operation not permitted")
        }

        var errorDescription: String? {
            switch self {
            case let .executing(command, error):
                return "executing: \(command) error: \(error)"
            case let .invalidBinaryPath(path):
                return "Invalid patch binary path: \(path)"
            }
        }
    }

    static func version(app: URL) async throws -> String? {
        let infoPlist = shellQuote(app.appendingPathComponent("Contents/Info.plist").path)
        return try await Command.execute(
            command: "/usr/bin/defaults read \(infoPlist) CFBundleVersion"
        )
    }

    static func patch(app: URL, config: Config) async throws {
        let appRoot = app.standardizedFileURL.resolvingSymlinksInPath()
        let entriesByBinary = Dictionary(grouping: config.targets, by: \.binary)

        for relativePath in entriesByBinary.keys.sorted() {
            let binary = app
                .appendingPathComponent(relativePath)
                .standardizedFileURL
                .resolvingSymlinksInPath()

            guard binary.path.hasPrefix(appRoot.path + "/") else {
                throw Error.invalidBinaryPath(relativePath)
            }

            let entries = entriesByBinary[relativePath, default: []].flatMap(\.entries)
            print("Patching: \(relativePath)")
            try Patcher.patch(binary: binary, entries: entries)
        }
    }

    static func resign(app: URL) async throws {
        let appPath = shellQuote(app.path)
        try await executeWithAdministratorFallback(
            command: "/usr/bin/xattr -cr \(appPath)"
        )
        try await executeWithAdministratorFallback(
            command: "/usr/bin/codesign --remove-sign \(appPath)"
        )
        try await executeWithAdministratorFallback(
            command: "/usr/bin/codesign --force --deep --sign - \(appPath)"
        )
        try await executeWithAdministratorFallback(
            command: "/usr/bin/codesign --verify --deep --strict \(appPath)"
        )
    }

    @discardableResult
    private static func executeWithAdministratorFallback(command: String) async throws -> String? {
        do {
            return try await execute(command: command)
        } catch let error as Command.Error where error.isPermissionDenied {
            print("Permission denied. Requesting administrator privileges...")
            return try await execute(
                command: command,
                administratorPrivileges: true
            )
        }
    }

    @discardableResult
    private static func execute(
        command: String,
        administratorPrivileges: Bool = false
    ) async throws -> String? {
        let escapedCommand = command
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "\"", with: "\\\"")
        let privileges = administratorPrivileges ? " with administrator privileges" : ""

        guard let script = NSAppleScript(
            source: "do shell script \"\(escapedCommand)\"\(privileges)"
        ) else {
            throw Error.executing(
                command: command,
                error: ["error": "Create script failed."]
            )
        }

        var error: NSDictionary?
        let descriptor = script.executeAndReturnError(&error)

        if let error = error {
            throw Error.executing(
                command: command,
                error: error
            )
        } else {
            return descriptor.stringValue
        }
    }

    private static func shellQuote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}
