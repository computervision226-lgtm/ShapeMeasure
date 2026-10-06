import SwiftUI

@main
@MainActor
struct ShapeMeasureApp: App {
    @StateObject private var store = ScanStore()
    var body: some Scene {
        WindowGroup {
            HomeView().environmentObject(store)
                .tint(Palette.mint)
                .preferredColorScheme(.dark)
        }
    }
}

enum Palette {
    static let background = Color(red: 0.035, green: 0.065, blue: 0.075)
    static let card = Color(red: 0.075, green: 0.115, blue: 0.125)
    static let mint = Color(red: 0.48, green: 0.9, blue: 0.77)
}

struct Panel<Content: View>: View {
    let content: Content
    init(@ViewBuilder content: () -> Content) { self.content = content() }
    var body: some View {
        VStack(alignment: .leading, spacing: 14) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(18).background(Palette.card, in: RoundedRectangle(cornerRadius: 20))
    }
}

struct FilledButton: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.headline).frame(maxWidth: .infinity).padding(.vertical, 15)
            .foregroundStyle(Palette.background)
            .background(Palette.mint.opacity(configuration.isPressed ? 0.65 : 1), in: RoundedRectangle(cornerRadius: 14))
    }
}
