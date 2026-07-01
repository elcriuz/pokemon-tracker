import SwiftUI

/// Cardshop-Ankauf-Zusammenfassung ("Bulk-Kassenband").
/// Sticky-Summenkarte oben — die eine Zahl, die am Tresen zählt, ist der AUSZAHLBETRAG (Ankauf).
/// Darunter Fortschritt, Filter/Sort und die Positionsliste mit "Markt → Ankauf" pro Karte.
/// Ausgelegt auf große Stapel: nach Wert sortieren, Fehler/High filtern, Ankaufsliste teilen.
struct AnkaufSummaryView: View {
    @ObservedObject var queue: ScanQueue
    var onOpenSettings: () -> Void
    var onOpenLink: (URL) -> Void

    // Ankaufssatz teilt sich denselben UserDefaults-Key wie AppConfig.buyRatePercent.
    @AppStorage("buyRatePercent") private var ratePercent: Int = 80

    @State private var filter: RowFilter = .alle
    @State private var sort: RowSort = .reihenfolge
    @State private var manualTarget: ScanJob?
    @State private var manualInput = ""

    enum RowFilter: String, CaseIterable, Identifiable {
        case alle = "Alle", fehler = "Fehler", high = "High"
        var id: String { rawValue }
    }
    enum RowSort: String, CaseIterable, Identifiable {
        case reihenfolge = "Reihenfolge", wert = "Wert", name = "Name"
        var id: String { rawValue }
    }

    var body: some View {
        VStack(spacing: 10) {
            summaryCard
            if !queue.jobs.isEmpty {
                controlBar
                list
            } else {
                emptyState
            }
        }
        .padding(12)
        .background(Color(.secondarySystemBackground))
        .alert("Preis manuell eintragen", isPresented: manualPresented, presenting: manualTarget) { job in
            TextField("z. B. 12,50", text: $manualInput).keyboardType(.decimalPad)
            Button("Übernehmen") { applyManual(job) }
            Button("Abbrechen", role: .cancel) { manualTarget = nil }
        } message: { job in
            Text("Marktwert (€) für \(displayName(job)) — zählt dann in die Ankaufssumme.")
        }
    }

    // MARK: - Sticky-Summenkarte

    private var summaryCard: some View {
        let market = queue.totalMarketEur
        let buy = queue.totalBuyEur(ratePercent: ratePercent)
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    rateMenu
                    Text(eur(buy))
                        .font(.system(size: 40, weight: .bold, design: .rounded))
                        .monospacedDigit()
                        .contentTransition(.numericText())
                        .animation(.snappy, value: buy)
                    Text("Marktwert \(eur(market))")
                        .font(.subheadline).foregroundStyle(.secondary)
                        .contentTransition(.numericText())
                        .animation(.snappy, value: market)
                }
                Spacer()
                VStack(spacing: 14) {
                    if !queue.jobs.isEmpty {
                        ShareLink(item: exportText) {
                            Image(systemName: "square.and.arrow.up").font(.title3)
                        }
                    }
                    Button(action: onOpenSettings) {
                        Image(systemName: "gearshape").font(.title3)
                    }
                }
                .foregroundStyle(.secondary)
            }
            if !queue.jobs.isEmpty { progressBlock }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18))
    }

    private var rateMenu: some View {
        Menu {
            Picker("Ankaufssatz", selection: $ratePercent) {
                ForEach([60, 65, 70, 75, 80, 85, 90], id: \.self) { Text("\($0) %").tag($0) }
            }
        } label: {
            HStack(spacing: 3) {
                Text("ANKAUF · \(ratePercent) %").font(.caption).fontWeight(.semibold).tracking(0.5)
                Image(systemName: "chevron.down").font(.caption2)
            }
            .foregroundStyle(.secondary)
        }
    }

    private var progressBlock: some View {
        let total = queue.jobs.count
        let done = queue.processedCount
        return VStack(alignment: .leading, spacing: 5) {
            ProgressView(value: Double(done), total: Double(max(total, 1)))
                .tint(.green)
            HStack(spacing: 10) {
                Label("\(done)/\(total)", systemImage: "checkmark.circle")
                if queue.inFlight > 0 {
                    Label("\(queue.inFlight)", systemImage: "arrow.triangle.2.circlepath")
                }
                if queue.noPriceCount > 0 {
                    Label("\(queue.noPriceCount) ohne Preis", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                }
                Spacer()
                Button("Leeren") { queue.clear() }
            }
            .font(.caption).foregroundStyle(.secondary)
        }
    }

    // MARK: - Filter & Sort

    private var controlBar: some View {
        HStack(spacing: 10) {
            Picker("Filter", selection: $filter) {
                ForEach(RowFilter.allCases) { f in
                    Text(f == .fehler && queue.noPriceCount > 0 ? "Fehler (\(queue.noPriceCount))"
                         : f == .high && queue.highValueCount > 0 ? "High (\(queue.highValueCount))"
                         : f.rawValue).tag(f)
                }
            }
            .pickerStyle(.segmented)

            Menu {
                Picker("Sortieren", selection: $sort) {
                    ForEach(RowSort.allCases) { Text($0.rawValue).tag($0) }
                }
            } label: {
                Image(systemName: "arrow.up.arrow.down").font(.subheadline)
                    .padding(6).background(.ultraThinMaterial, in: Circle())
            }
        }
    }

    // MARK: - Positionsliste

    private var list: some View {
        ScrollView {
            LazyVStack(spacing: 8) {
                ForEach(visibleJobs) { job in
                    AnkaufRow(job: job, ratePercent: ratePercent,
                              onOpenLink: onOpenLink,
                              onRetry: { queue.retry(job.id) },
                              onManual: { manualInput = ""; manualTarget = job })
                }
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 6) {
            Image(systemName: "tray.full").font(.title2).foregroundStyle(.secondary)
            Text("Karten abfeuern → Ankaufssumme baut sich hier live auf.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.vertical, 24)
    }

    // MARK: - Ableitungen

    private var visibleJobs: [ScanJob] {
        var rows = queue.jobs.filter { job in
            switch filter {
            case .alle: return true
            case .fehler:
                return job.status == .failed || (job.status == .done && (job.result?.marketEur ?? 0) <= 0)
            case .high:
                return (job.result?.marketEur ?? 0) >= ScanQueue.highValueThreshold
            }
        }
        switch sort {
        case .reihenfolge: break   // queue.jobs ist bereits neueste-zuerst
        case .wert: rows.sort { ($0.result?.marketEur ?? -1) > ($1.result?.marketEur ?? -1) }
        case .name: rows.sort { displayName($0).localizedCaseInsensitiveCompare(displayName($1)) == .orderedAscending }
        }
        return rows
    }

    private var exportText: String {
        var lines = ["Ankauf-Zusammenfassung (\(ratePercent) %)",
                     "Marktwert gesamt: \(eur(queue.totalMarketEur))",
                     "Ankauf gesamt:    \(eur(queue.totalBuyEur(ratePercent: ratePercent)))",
                     ""]
        let sorted = queue.jobs.sorted { ($0.result?.marketEur ?? -1) > ($1.result?.marketEur ?? -1) }
        for job in sorted {
            let name = displayName(job)
            let meta = [job.result?.number.map { "#\($0)" }, job.result?.grade.flatMap { $0 == "raw" ? nil : $0 }]
                .compactMap { $0 }.joined(separator: " ")
            let head = meta.isEmpty ? name : "\(name) \(meta)"
            if let m = job.result?.marketEur, m > 0 {
                lines.append("\(head) — Markt \(eur(m)) → Ankauf \(eur(m * Double(ratePercent) / 100))")
            } else {
                lines.append("\(head) — kein Preis")
            }
        }
        return lines.joined(separator: "\n")
    }

    private var manualPresented: Binding<Bool> {
        Binding(get: { manualTarget != nil }, set: { if !$0 { manualTarget = nil } })
    }

    private func applyManual(_ job: ScanJob) {
        let cleaned = manualInput.replacingOccurrences(of: ",", with: ".")
            .trimmingCharacters(in: .whitespaces)
        if let v = Double(cleaned), v > 0 { queue.setManualPrice(job.id, eur: v) }
        manualTarget = nil
    }

    private func displayName(_ job: ScanJob) -> String {
        job.result?.name ?? job.hints.name ?? "wird erkannt…"
    }
}

/// Eine Positionszeile: Name, Nr./Grade und "Markt → Ankauf" — oder Nacharbeits-Aktionen bei fehlendem Preis.
private struct AnkaufRow: View {
    let job: ScanJob
    let ratePercent: Int
    var onOpenLink: (URL) -> Void
    var onRetry: () -> Void
    var onManual: () -> Void

    private var market: Double? { job.result?.marketEur.flatMap { $0 > 0 ? $0 : nil } }
    private var isHigh: Bool { (market ?? 0) >= ScanQueue.highValueThreshold }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            statusGlyph
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 5) {
                    Text(job.result?.name ?? job.hints.name ?? "wird erkannt…")
                        .fontWeight(.semibold).lineLimit(1)
                    if isHigh { Image(systemName: "star.fill").font(.caption2).foregroundStyle(.yellow) }
                }
                if let meta = metaLine { Text(meta).font(.caption).foregroundStyle(.secondary) }
                if market == nil && (job.status == .done || job.status == .failed) {
                    HStack(spacing: 8) {
                        Button(action: onRetry) { Label("Preis suchen", systemImage: "magnifyingglass") }
                        Button(action: onManual) { Label("manuell", systemImage: "pencil") }
                    }
                    .font(.caption).buttonStyle(.bordered).controlSize(.mini)
                    .padding(.top, 2)
                }
            }
            Spacer()
            trailing
        }
        .padding(10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder private var trailing: some View {
        if let m = market {
            HStack(spacing: 8) {
                VStack(alignment: .trailing, spacing: 1) {
                    Text(eur(m * Double(ratePercent) / 100))
                        .fontWeight(.semibold).monospacedDigit()
                    Text(eur(m)).font(.caption2).foregroundStyle(.secondary)
                        .strikethrough(false).monospacedDigit()
                }
                if let cm = job.result?.cmUrl, let url = URL(string: cm) {
                    Button { onOpenLink(url) } label: { Image(systemName: "arrow.up.forward.square") }
                        .foregroundStyle(.secondary)
                }
            }
        } else if job.status == .processing || job.status == .queued {
            ProgressView().scaleEffect(0.7)
        } else if job.status == .failed {
            Image(systemName: "wifi.slash").foregroundStyle(.red)
        }
    }

    private var statusGlyph: some View {
        Circle()
            .fill(job.status == .failed ? Color.red
                  : market != nil ? .green
                  : job.status == .processing || job.status == .queued ? .orange
                  : .gray)
            .frame(width: 9, height: 9)
            .padding(.top, 5)
    }

    private var metaLine: String? {
        var parts: [String] = []
        if let n = job.result?.number ?? job.hints.collectorNumber { parts.append("#\(n)") }
        if let g = job.result?.grade, g != "raw" { parts.append(g) }
        if job.result?.via == "manual" { parts.append("manuell") }
        else if job.result?.via.contains("on-device") == true { parts.append("nur On-Device") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

// MARK: - €-Formatierung (de_DE: "1.234,80 €")

private let eurFormatter: NumberFormatter = {
    let f = NumberFormatter()
    f.numberStyle = .decimal
    f.minimumFractionDigits = 2
    f.maximumFractionDigits = 2
    f.locale = Locale(identifier: "de_DE")
    return f
}()

func eur(_ v: Double) -> String { (eurFormatter.string(from: NSNumber(value: v)) ?? "0,00") + " €" }
