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
        try await Command.execute(command: "defaults read \(app.appendingPathComponent("Contents/Info.plist").path) CFBundleVersion")
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
        try await Command.execute(command: "codesign --remove-sign \(app.path)")
        try await Command.execute(command: "codesign --force --deep --sign - \(app.path)")
        try await Command.execute(command: "xattr -cr \(app.path)")
    }

    @discardableResult
    private static func execute(command: String) async throws -> String? {
        guard let script = NSAppleScript(source: "do shell script \"\(command)\"") else {
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
}
