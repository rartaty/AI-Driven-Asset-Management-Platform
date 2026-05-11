"use client";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip } from 'recharts';


// eslint-disable-next-line @typescript-eslint/no-explicit-any
export default function PortfolioChart({ data, timeframe = "daily" }: { data: any[], timeframe?: string }) {
  const formatYen = (amount: number) => {
    return new Intl.NumberFormat("ja-JP", { style: "currency", currency: "JPY" }).format(amount);
  };

  if (!data || data.length === 0) {
    return <div className="h-[350px] w-full flex items-center justify-center text-slate-500">No Chart Data Available</div>;
  }

  const isIntraday = timeframe === "intraday";
  const dataKey = isIntraday ? "pnl" : "value";
  const strokeColor = isIntraday ? "#00E5FF" : "#B2FF05"; // Day Blue for intraday, Stem Green for long term

  return (
    <div className="w-full h-[350px] overflow-x-auto overflow-y-hidden rounded-lg mt-4">
      <AreaChart width={650} height={300} data={data} margin={{ top: 10, right: 30, left: 20, bottom: 0 }}>
        <defs>
          <linearGradient id="colorMain" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor={strokeColor} stopOpacity={0.4}/>
            <stop offset="95%" stopColor={strokeColor} stopOpacity={0}/>
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
        <XAxis 
          dataKey="date" 
          stroke="#94a3b8" 
          fontSize={11} 
          tickLine={false} 
          axisLine={false} 
          tickMargin={10}
        />
        <YAxis 
          stroke="#94a3b8" 
          fontSize={11} 
          tickLine={false} 
          axisLine={false} 
          tickFormatter={(value) => `¥${(value / 10000).toFixed(0)}万`} 
          tickMargin={10}
        />
        <Tooltip 
          contentStyle={{ backgroundColor: 'rgba(20,20,20,0.8)', backdropFilter: 'blur(10px)', borderColor: 'rgba(255,255,255,0.1)', borderRadius: '12px', color: '#fff', boxShadow: '0 10px 25px -5px rgba(0, 0, 0, 0.5)' }}
          itemStyle={{ fontSize: '14px', color: strokeColor, fontWeight: 'bold' }}
          formatter={(value: number) => [formatYen(value), isIntraday ? "Intraday PnL" : "Total Value"]}
          labelStyle={{ color: '#94a3b8', marginBottom: '4px' }}
        />
        <Area 
          type="monotone" 
          dataKey={dataKey} 
          stroke={strokeColor} 
          strokeWidth={3} 
          fillOpacity={1}
          fill="url(#colorMain)" 
          activeDot={{ r: 6, strokeWidth: 0, fill: '#fff', style: { filter: `drop-shadow(0 0 8px ${strokeColor})` } }}
        />
      </AreaChart>
    </div>
  );
}
