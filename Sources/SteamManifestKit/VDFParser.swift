import Foundation

public enum VDFParseError: LocalizedError, Equatable {
    case unexpectedEndOfInput(position: Int)
    case expectedKey(position: Int)
    case expectedValue(position: Int)
    case unexpectedToken(position: Int)
    case unterminatedString(position: Int)

    public var errorDescription: String? {
        switch self {
        case .unexpectedEndOfInput(let position):
            return "Unexpected end of VDF input at character \(position)."
        case .expectedKey(let position):
            return "Expected a VDF key at token \(position)."
        case .expectedValue(let position):
            return "Expected a VDF value at token \(position)."
        case .unexpectedToken(let position):
            return "Unexpected VDF token at character \(position)."
        case .unterminatedString(let position):
            return "Unterminated VDF string starting at character \(position)."
        }
    }
}

public enum VDFParser {
    private enum Token {
        case string(String)
        case openBrace
        case closeBrace
    }

    public static func parse(_ text: String) throws -> [String: VDFValue] {
        let tokens = try tokenize(text)
        var index = 0
        var root: [String: VDFValue] = [:]

        while index < tokens.count {
            guard case .string(let key) = tokens[index] else {
                if case .closeBrace = tokens[index] {
                    throw VDFParseError.expectedKey(position: index)
                }
                throw VDFParseError.expectedKey(position: index)
            }

            index += 1
            guard index < tokens.count else {
                throw VDFParseError.unexpectedEndOfInput(position: index)
            }

            switch tokens[index] {
            case .openBrace:
                index += 1
                var object: [String: VDFValue] = [:]
                try parseObject(
                    tokens: tokens,
                    index: &index,
                    into: &object
                )
                root[key] = .object(object)

            case .string(let value):
                root[key] = .string(value)
                index += 1

            case .closeBrace:
                throw VDFParseError.expectedValue(position: index)
            }
        }

        return root
    }

    private static func parseObject(
        tokens: [Token],
        index: inout Int,
        into object: inout [String: VDFValue]
    ) throws {
        while index < tokens.count {
            if case .closeBrace = tokens[index] {
                index += 1
                return
            }

            guard case .string(let key) = tokens[index] else {
                throw VDFParseError.expectedKey(position: index)
            }

            index += 1
            guard index < tokens.count else {
                throw VDFParseError.unexpectedEndOfInput(position: index)
            }

            switch tokens[index] {
            case .openBrace:
                index += 1
                var child: [String: VDFValue] = [:]
                try parseObject(
                    tokens: tokens,
                    index: &index,
                    into: &child
                )
                object[key] = .object(child)

            case .string(let value):
                object[key] = .string(value)
                index += 1

            case .closeBrace:
                throw VDFParseError.expectedValue(position: index)
            }
        }

        throw VDFParseError.unexpectedEndOfInput(position: index)
    }

    private static func tokenize(_ text: String) throws -> [Token] {
        let characters = Array(text)
        var tokens: [Token] = []
        var index = 0

        while index < characters.count {
            let character = characters[index]

            if index == 0, character == "\u{feff}" {
                index += 1
                continue
            }

            if character.isWhitespace {
                index += 1
                continue
            }

            if character == "/",
               index + 1 < characters.count,
               characters[index + 1] == "/" {
                while index < characters.count, characters[index] != "\n" {
                    index += 1
                }
                continue
            }

            if character == "{" {
                tokens.append(.openBrace)
                index += 1
                continue
            }

            if character == "}" {
                tokens.append(.closeBrace)
                index += 1
                continue
            }

            if character == "\"" {
                let start = index
                index += 1
                var value = ""
                var terminated = false

                while index < characters.count {
                    let current = characters[index]

                    if current == "\"" {
                        terminated = true
                        index += 1
                        break
                    }

                    if current == "\\" {
                        index += 1
                        guard index < characters.count else {
                            throw VDFParseError.unterminatedString(position: start)
                        }

                        let escaped = characters[index]
                        switch escaped {
                        case "n":
                            value.append("\n")
                        case "r":
                            value.append("\r")
                        case "t":
                            value.append("\t")
                        case "\"":
                            value.append("\"")
                        case "\\":
                            value.append("\\")
                        default:
                            value.append(escaped)
                        }

                        index += 1
                        continue
                    }

                    value.append(current)
                    index += 1
                }

                guard terminated else {
                    throw VDFParseError.unterminatedString(position: start)
                }

                tokens.append(.string(value))
                continue
            }

            let start = index
            var value = ""

            while index < characters.count {
                let current = characters[index]
                if current.isWhitespace || current == "{" || current == "}" {
                    break
                }

                value.append(current)
                index += 1
            }

            guard !value.isEmpty else {
                throw VDFParseError.unexpectedToken(position: start)
            }

            tokens.append(.string(value))
        }

        return tokens
    }
}
