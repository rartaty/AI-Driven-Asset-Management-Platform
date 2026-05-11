import { Position, formatYen } from "./types";

export default function PositionsTable({ positions }: { positions: Position[] }) {
  return (
    <div className="glass-card p-6 overflow-hidden">
      <h3 className="text-lg font-semibold text-white mb-4 drop-shadow-md">Active Positions</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm text-slate-300">
          <thead className="text-xs text-slate-500 uppercase border-b border-white/10">
            <tr>
              <th className="px-4 py-3">Symbol</th>
              <th className="px-4 py-3">Category</th>
              <th className="px-4 py-3 text-right">Shares</th>
              <th className="px-4 py-3 text-right">Avg Price</th>
              <th className="px-4 py-3 text-right">Current</th>
              <th className="px-4 py-3 text-right">Unrealized PNL</th>
            </tr>
          </thead>
          <tbody>
            {(positions || []).map((pos, idx) => {
              let badgeColor = "bg-slate-500/20 text-slate-300 border-slate-500/30";
              if (pos.category === "Passive") badgeColor = "bg-blue-500/20 text-[#00E5FF] border-[#00E5FF]/30";
              if (pos.category === "Long_Solid") badgeColor = "bg-green-500/20 text-[#B2FF05] border-[#B2FF05]/30";
              if (pos.category === "Long_Growth") badgeColor = "bg-purple-500/20 text-purple-300 border-purple-500/30";
              if (pos.category === "Short") badgeColor = "bg-[#FF3366]/20 text-[#FF3366] border-[#FF3366]/30";

              return (
              <tr key={idx} className="border-b border-white/5 hover:bg-white/5 transition-colors">
                <td className="px-4 py-3 font-medium text-white">
                  {pos.symbol} <span className="block text-xs text-slate-500">{pos.name}</span>
                </td>
                <td className="px-4 py-3">
                  <span className={`px-2 py-1 rounded text-xs border ${badgeColor}`}>
                    {pos.category}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">{pos.shares.toLocaleString()}</td>
                <td className="px-4 py-3 text-right text-slate-400">¥{pos.avg_price.toLocaleString()}</td>
                <td className="px-4 py-3 text-right">¥{pos.current_price.toLocaleString()}</td>
                <td className={`px-4 py-3 text-right font-bold ${pos.unrealized_pnl >= 0 ? 'text-[#B2FF05] drop-shadow-[0_0_8px_rgba(178,255,5,0.3)]' : 'text-[#FF3366] drop-shadow-[0_0_8px_rgba(255,51,102,0.3)]'}`}>
                  {pos.unrealized_pnl >= 0 ? '+' : ''}{formatYen(pos.unrealized_pnl)}
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
