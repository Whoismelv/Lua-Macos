import Foundation

public enum VDFValue: Equatable {
    case string(String)
    case object([String: VDFValue])

    public var stringValue: String? {
        if case .string(let value) = self {
            return value
        }
        return nil
    }

    public var objectValue: [String: VDFValue]? {
        if case .object(let value) = self {
            return value
        }
        return nil
    }

    public func string(forKey key: String) -> String? {
        lookup(key)?.stringValue
    }

    public func object(forKey key: String) -> [String: VDFValue]? {
        lookup(key)?.objectValue
    }

    private func lookup(_ key: String) -> VDFValue? {
        guard let object = objectValue else {
            return nil
        }

        if let exact = object[key] {
            return exact
        }

        return object.first {
            $0.key.caseInsensitiveCompare(key) == .orderedSame
        }?.value
    }

    public var jsonObject: Any {
        switch self {
        case .string(let value):
            return value
        case .object(let value):
            return value.mapValues { $0.jsonObject }
        }
    }
}
