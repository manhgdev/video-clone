import AVFoundation
import CoreImage
import CoreImage.CIFilterBuiltins
import Foundation
import Metal

struct Mask: Decodable {
    let start: Double
    let end: Double
    let x: Double
    let y: Double
    let width: Double
    let height: Double
    let radius: Double
    let tintRed: Double
    let tintGreen: Double
    let tintBlue: Double
    let tintAlpha: Double
}

enum BlurError: Error {
    case invalidArguments
    case noVideoTrack
    case noMetalDevice
    case cannotCreateExporter
    case exportFailed(String)
}

func clampedRect(_ mask: Mask, in extent: CGRect) -> CGRect? {
    let rect = CGRect(x: mask.x, y: mask.y, width: mask.width, height: mask.height)
        .intersection(extent)
        .integral
    return rect.width >= 8 && rect.height >= 8 ? rect : nil
}

func blurredMask(_ image: CIImage, mask: Mask) -> CIImage {
    guard let rect = clampedRect(mask, in: image.extent) else { return image }
    let padding = max(CGFloat(mask.radius) + 4, 16)
    let expanded = rect.insetBy(dx: -padding, dy: -padding).intersection(image.extent)
    let clamp = CIFilter.affineClamp()
    clamp.inputImage = image.cropped(to: expanded)
    let gaussian = CIFilter.gaussianBlur()
    gaussian.inputImage = clamp.outputImage
    gaussian.radius = Float(max(0.5, mask.radius / 2.0))
    let desaturate = CIFilter.colorControls()
    desaturate.inputImage = gaussian.outputImage?.cropped(to: rect)
    desaturate.saturation = 0.88
    let tinted = CIImage(
        color: CIColor(
            red: CGFloat(mask.tintRed),
            green: CGFloat(mask.tintGreen),
            blue: CGFloat(mask.tintBlue),
            alpha: CGFloat(mask.tintAlpha)
        )
    ).cropped(to: rect)
    let composite = CIFilter.sourceOverCompositing()
    composite.inputImage = tinted
    composite.backgroundImage = desaturate.outputImage
    return (composite.outputImage ?? desaturate.outputImage ?? image.cropped(to: rect))
        .cropped(to: rect)
        .composited(over: image)
}

func run() throws {
    let args = CommandLine.arguments
    guard args.count == 4 else { throw BlurError.invalidArguments }
    let input = URL(fileURLWithPath: args[1])
    let output = URL(fileURLWithPath: args[2])
    let masks = try JSONDecoder().decode([Mask].self, from: Data(contentsOf: URL(fileURLWithPath: args[3])))
    guard !masks.isEmpty else { throw BlurError.invalidArguments }
    guard let device = MTLCreateSystemDefaultDevice() else { throw BlurError.noMetalDevice }

    let asset = AVURLAsset(url: input)
    guard let track = asset.tracks(withMediaType: .video).first else { throw BlurError.noVideoTrack }
    let context = CIContext(mtlDevice: device, options: [.useSoftwareRenderer: false])

    // AVVideoComposition's Core Image handler keeps pixels on Metal for the
    // complete ROI blur/composite path; the export session encodes the result.
    let ciComposition = AVVideoComposition(asset: asset) { request in
        let seconds = request.compositionTime.seconds
        var image = request.sourceImage
        for mask in masks where seconds >= mask.start && seconds < mask.end {
            image = blurredMask(image, mask: mask)
        }
        request.finish(with: image, context: context)
    }

    try? FileManager.default.removeItem(at: output)
    guard let exporter = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetHighestQuality) else {
        throw BlurError.cannotCreateExporter
    }
    exporter.outputURL = output
    exporter.outputFileType = .mp4
    exporter.videoComposition = ciComposition
    exporter.exportAsynchronously {}
    while exporter.status == .waiting || exporter.status == .exporting {
        RunLoop.current.run(until: Date(timeIntervalSinceNow: 0.05))
    }
    guard exporter.status == .completed else {
        throw BlurError.exportFailed(exporter.error?.localizedDescription ?? "unknown export error")
    }
}

do {
    try run()
} catch {
    FileHandle.standardError.write(Data("apple_ci_blur: \(error)\n".utf8))
    exit(1)
}
