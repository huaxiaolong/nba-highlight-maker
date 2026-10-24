import Foundation
import Vision
import ImageIO
import CoreGraphics

// shortcut: calibrated for this ESPN scorebug; recalibrate these regions for another broadcaster.
let espnRegions: [(String, CGRect)] = [
    ("clock", CGRect(x: 0.623, y: 0.875, width: 0.099, height: 0.055)),
    ("period", CGRect(x: 0.587, y: 0.875, width: 0.039, height: 0.055)),
    ("away", CGRect(x: 0.309, y: 0.875, width: 0.137, height: 0.065)),
    ("home", CGRect(x: 0.4375, y: 0.8736, width: 0.1602, height: 0.0764)),
]

let spectrumRegions: [(String, CGRect)] = [
    ("clock", CGRect(x: 0.802, y: 0.842, width: 0.070, height: 0.045)),
    ("period", CGRect(x: 0.738, y: 0.842, width: 0.060, height: 0.045)),
    ("away", CGRect(x: 0.732, y: 0.778, width: 0.095, height: 0.065)),
    ("home", CGRect(x: 0.828, y: 0.778, width: 0.104, height: 0.065)),
]
let profile = CommandLine.arguments.dropFirst().first ?? "espn"
guard ["espn", "spectrum"].contains(profile) else {
    fputs("Unknown OCR profile\n", stderr)
    exit(1)
}
let regions = profile == "spectrum" ? spectrumRegions : espnRegions

func recognize(_ path: String) throws -> [String: Any] {
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
        throw NSError(domain: "NBAOCR", code: 1, userInfo: [NSLocalizedDescriptionKey: "Cannot decode image: \(path)"])
    }
    var output: [String: Any] = ["file": path]
    for (name, region) in regions {
        let rect = CGRect(x: region.minX * Double(image.width), y: region.minY * Double(image.height),
                          width: region.width * Double(image.width), height: region.height * Double(image.height))
        guard let crop = image.cropping(to: rect) else { continue }
        let request = VNRecognizeTextRequest()
        // Revision 2 reads isolated score digits with their team label more reliably in this scorebug.
        if name == "home" || name == "away" { request.revision = 2 }
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["en-US"]
        request.usesLanguageCorrection = false
        try VNImageRequestHandler(cgImage: crop).perform([request])
        output[name] = (request.results ?? []).compactMap { observation -> [String: Any]? in
            guard let text = observation.topCandidates(1).first else { return nil }
            return ["text": text.string, "confidence": text.confidence]
        }
    }
    return output
}

// One JSON object per input path; one process amortizes Vision/model startup.
while let path = readLine() {
    if path.isEmpty { continue }
    autoreleasepool {
        do {
            let data = try JSONSerialization.data(withJSONObject: recognize(path), options: [.sortedKeys])
            print(String(decoding: data, as: UTF8.self))
        } catch {
            let e = error as NSError
            let output: [String: Any] = ["file": path, "error": e.localizedDescription,
                                         "domain": e.domain, "code": e.code]
            if let data = try? JSONSerialization.data(withJSONObject: output) {
                print(String(decoding: data, as: UTF8.self))
            }
        }
        fflush(stdout)
    }
}
