import Compression
import Foundation

/// Minimal PNG encoder for the LiDAR depth (16-bit grey, millimetres) and confidence (8-bit grey) maps.
/// Self-contained so the files are exactly what the reconstruction code expects, on every iOS version.
enum PNGWriter {
    @discardableResult
    static func writeGray16(_ pixels: [UInt16], width: Int, height: Int, to url: URL) -> Bool {
        guard pixels.count == width * height else { return false }
        var raw = [UInt8]()
        raw.reserveCapacity(height * (1 + 2 * width))
        for y in 0..<height {
            raw.append(0)                               // filter type: none
            let row = y * width
            for x in 0..<width {
                let v = pixels[row + x]
                raw.append(UInt8(v >> 8))
                raw.append(UInt8(v & 0xFF))
            }
        }
        return write(raw: raw, width: width, height: height, bitDepth: 16, to: url)
    }

    @discardableResult
    static func writeGray8(_ pixels: [UInt8], width: Int, height: Int, to url: URL) -> Bool {
        guard pixels.count == width * height else { return false }
        var raw = [UInt8]()
        raw.reserveCapacity(height * (1 + width))
        for y in 0..<height {
            raw.append(0)
            raw.append(contentsOf: pixels[(y * width)..<((y + 1) * width)])
        }
        return write(raw: raw, width: width, height: height, bitDepth: 8, to: url)
    }

    // MARK: - container

    private static func write(raw: [UInt8], width: Int, height: Int, bitDepth: UInt8, to url: URL) -> Bool {
        var png = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])
        var ihdr = [UInt8]()
        ihdr.append(contentsOf: be32(UInt32(width)))
        ihdr.append(contentsOf: be32(UInt32(height)))
        ihdr.append(contentsOf: [bitDepth, 0, 0, 0, 0])   // greyscale, deflate, adaptive filtering, no interlace
        png.append(chunk("IHDR", ihdr))
        png.append(chunk("IDAT", zlib(raw)))
        png.append(chunk("IEND", []))
        do {
            try png.write(to: url, options: .atomic)
            return true
        } catch {
            return false
        }
    }

    private static func chunk(_ type: String, _ body: [UInt8]) -> Data {
        var d = Data()
        d.append(contentsOf: be32(UInt32(body.count)))
        let typeBytes = [UInt8](type.utf8)
        d.append(contentsOf: typeBytes)
        d.append(contentsOf: body)
        d.append(contentsOf: be32(crc32(typeBytes + body)))
        return d
    }

    private static func be32(_ v: UInt32) -> [UInt8] {
        [UInt8(v >> 24 & 0xFF), UInt8(v >> 16 & 0xFF), UInt8(v >> 8 & 0xFF), UInt8(v & 0xFF)]
    }

    /// zlib stream (RFC 1950) around raw DEFLATE data from the Compression framework, which produces
    /// RFC 1951 data without the zlib header and checksum. Falls back to stored blocks if encoding fails.
    private static func zlib(_ src: [UInt8]) -> [UInt8] {
        var out: [UInt8] = [0x78, 0x9C]
        out.append(contentsOf: deflate(src))
        out.append(contentsOf: be32(adler32(src)))
        return out
    }

    private static func deflate(_ src: [UInt8]) -> [UInt8] {
        let capacity = src.count + src.count / 8 + 1024
        var dst = [UInt8](repeating: 0, count: capacity)
        let n = src.withUnsafeBufferPointer { s -> Int in
            dst.withUnsafeMutableBufferPointer { d -> Int in
                guard let sp = s.baseAddress, let dp = d.baseAddress else { return 0 }
                return compression_encode_buffer(dp, capacity, sp, src.count, nil, COMPRESSION_ZLIB)
            }
        }
        if n > 0 {
            return Array(dst[0..<n])
        }
        return stored(src)
    }

    /// Uncompressed DEFLATE blocks (used only if the encoder fails).
    private static func stored(_ src: [UInt8]) -> [UInt8] {
        var out = [UInt8]()
        var i = 0
        repeat {
            let len = min(65535, src.count - i)
            let last: UInt8 = (i + len >= src.count) ? 1 : 0
            out.append(last)
            out.append(UInt8(len & 0xFF)); out.append(UInt8(len >> 8))
            let nlen = ~UInt16(len)
            out.append(UInt8(nlen & 0xFF)); out.append(UInt8(nlen >> 8))
            out.append(contentsOf: src[i..<(i + len)])
            i += len
        } while i < src.count
        return out
    }

    private static func adler32(_ data: [UInt8]) -> UInt32 {
        var a: UInt32 = 1, b: UInt32 = 0
        for byte in data {
            a = (a + UInt32(byte)) % 65521
            b = (b + a) % 65521
        }
        return (b << 16) | a
    }

    private static let crcTable: [UInt32] = (0..<256).map { n -> UInt32 in
        var c = UInt32(n)
        for _ in 0..<8 {
            c = (c & 1) != 0 ? (0xEDB88320 ^ (c >> 1)) : (c >> 1)
        }
        return c
    }

    private static func crc32(_ data: [UInt8]) -> UInt32 {
        var c: UInt32 = 0xFFFFFFFF
        for byte in data {
            c = crcTable[Int((c ^ UInt32(byte)) & 0xFF)] ^ (c >> 8)
        }
        return c ^ 0xFFFFFFFF
    }
}
