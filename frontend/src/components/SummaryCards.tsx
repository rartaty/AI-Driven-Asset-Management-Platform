import { PortfolioSummary, formatYen } from "./types";

export default function SummaryCards({ data }: { data: PortfolioSummary }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
      <div className="md:col-span-2 glass-card p-6 border-l-[3px] border-l-[#B2FF05]">
        <h2 className="text-[#B2FF05] text-xs font-bold uppercase tracking-widest mb-2 drop-shadow-[0_0_8px_rgba(178,255,5,0.4)]">Total Asset Value</h2>
        <div className="text-4xl font-extrabold text-white tracking-tight">
          {formatYen(data.total_value)}
        </div>
      </div>

      {[
        { label: "Cash Balance", value: data.cash_balance, color: "text-white", border: "border-white/10", accent: "border-b-[#00E5FF]", glow: "shadow-[0_4px_15px_-3px_rgba(0,229,255,0.1)]" },
        { label: "Trust Funds", value: data.trust_value, color: "text-white", border: "border-white/10", accent: "border-b-[#B2FF05]", glow: "shadow-[0_4px_15px_-3px_rgba(178,255,5,0.1)]" },
        { label: "Long Term", value: data.long_value, color: "text-white", border: "border-white/10", accent: "border-b-[#FF3366]", glow: "shadow-[0_4px_15px_-3px_rgba(255,51,102,0.1)]" },
      ].map((item, idx) => (
        <div key={idx} className={`p-4 glass-panel border ${item.border} border-b-[3px] ${item.accent} ${item.glow} hover:-translate-y-1 transition-all duration-300`}>
          <h3 className="text-slate-400 text-xs font-medium mb-1">{item.label}</h3>
          <div className={`text-xl font-bold ${item.color}`}>{formatYen(item.value)}</div>
        </div>
      ))}
    </div>
  );
}
