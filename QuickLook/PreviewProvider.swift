// Quick Look preview of .glyphs files with VariableStroke's strokes as outlines.
//
// When Glyphs opens or saves a font with strokes in its "Ag", the VariableStroke
// tool keeps a large picture of that "Ag", strokes expanded, in the file's
// extended attributes (see file_preview.py). That picture is shown while it was
// made for the file as it is now; otherwise (no strokes, saved without the
// plugin, or changed elsewhere since) the preview is Glyphs' own thumbnail of
// the file.

import Cocoa
import Quartz
import QuickLookThumbnailing

private let previewAttribute = "com.codex.VariableStroke.preview"
private let stampAttribute = "com.codex.VariableStroke.previewStamp"
private let previewVersion = 2
private let side: CGFloat = 800  // points, as file_preview.SIZE

private func extendedAttribute(_ path: String, _ name: String) -> Data? {
    let size = getxattr(path, name, nil, 0, 0, 0)
    guard size >= 0 else { return nil }
    var data = Data(count: size)
    let read = data.withUnsafeMutableBytes { getxattr(path, name, $0.baseAddress, size, 0, 0) }
    return read == size ? data : nil
}

/// The modification time and size of the file, as Python's os.path reports them.
private func stamp(_ path: String) -> (Double, Int64)? {
    var info = stat()
    guard stat(path, &info) == 0 else { return nil }
    let time = Double(info.st_mtimespec.tv_sec) + Double(info.st_mtimespec.tv_nsec) * 1e-9
    return (time, Int64(info.st_size))
}

/// The PNG kept with the file, if it was made for the file as it is now.
private func currentPicture(_ path: String) -> Data? {
    guard let stampData = extendedAttribute(path, stampAttribute),
          let made = try? JSONSerialization.jsonObject(with: stampData) as? [String: Any],
          made["version"] as? Int == previewVersion,
          let madeFor = made["stamp"] as? [NSNumber], madeFor.count == 2,
          let now = stamp(path),
          abs(madeFor[0].doubleValue - now.0) < 1e-6, madeFor[1].int64Value == now.1,
          let picture = extendedAttribute(path, previewAttribute) else { return nil }
    return picture
}

/// What the last preview showed, for finding out why a preview looks as it does:
/// ~/Library/Containers/com.codex.VariableStroke.QuickLook.Preview/Data/tmp/last-preview.txt
private func note(_ url: URL, _ shown: String) {
    let line = "\(Date()) \(shown) \(url.path)\n"
    try? line.write(to: FileManager.default.temporaryDirectory.appendingPathComponent("last-preview.txt"),
                    atomically: true, encoding: .utf8)
}

@objc(PreviewProvider)
class PreviewProvider: QLPreviewProvider, QLPreviewingController {
    func providePreview(for request: QLFilePreviewRequest) async throws -> QLPreviewReply {
        let url = request.fileURL
        var image: CGImage? = nil
        if let picture = currentPicture(url.path),
           let source = CGImageSourceCreateWithData(picture as CFData, nil) {
            image = CGImageSourceCreateImageAtIndex(source, 0, nil)
        }
        note(url, image != nil ? "picture" : "thumbnail")
        if image == nil {
            let thumbnail = QLThumbnailGenerator.Request(fileAt: url, size: CGSize(width: side, height: side),
                                                         scale: 2, representationTypes: .thumbnail)
            image = try await QLThumbnailGenerator.shared.generateBestRepresentation(for: thumbnail).cgImage
        }
        let size = CGSize(width: side, height: side)
        return QLPreviewReply(contextSize: size, isBitmap: true) { context, _ in
            guard let image = image else { return }
            let scale = min(side / CGFloat(image.width), side / CGFloat(image.height))
            let width = CGFloat(image.width) * scale, height = CGFloat(image.height) * scale
            context.draw(image, in: CGRect(x: (side - width) / 2, y: (side - height) / 2,
                                           width: width, height: height))
        }
    }
}
