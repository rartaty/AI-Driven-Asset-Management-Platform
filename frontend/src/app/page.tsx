"use client";

import { useEffect, useState } from "react";
import dynamic from 'next/dynamic';

import { PortfolioSummary } from "@/components/types";
import SummaryCards from "@/components/SummaryCards";
import PositionsTable from "@/components/PositionsTable";
import ActivityLog from "@/components/ActivityLog";

const PortfolioChart = dynamic(() => import('@/components/PortfolioChart'), { 
  ssr: false, 
  loading: () => <div className="h-[350px] w-full flex items-center justify-center text-slate-500 bg-slate-950/30 rounded-lg">Loading Chart Engine...</div> 
});

export default function Dashboard() {
  const [data, setData] = useState<PortfolioSummary | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [chartData, setChartData] = useState<any[]>([]);
  const [timeframe, setTimeframe] = useState<"daily" | "monthly" | "intraday">("daily");
  const [loading, setLoading] = useState(true);
  const [chartLoading, setChartLoading] = useState(false);

  useEffect(() => {
    // 認証付きプロキシ経由でバックエンドへアクセス
    fetch("/api/v1/portfolio/summary", { cache: "no-store" })
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        return res.json();
      })
      .then((json) => {
        setData(json);
        setChartData(json.chart_data || []);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to fetch portfolio:", err);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!data) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setChartLoading(true);
    fetch(`/api/v1/portfolio/chart/${timeframe}`, { cache: "no-store" })
      .then(res => res.json())
      .then(json => {
        setChartData(json.chart_data || []);
        setChartLoading(false);
      })
      .catch(err => {
        console.error("Failed to fetch chart:", err);
        setChartLoading(false);
      });
  }, [timeframe, data]);

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-pulse text-slate-400">Loading Portfolio Data...</div></div>;
  }

  if (!data) {
    return <div className="text-red-400">Failed to load data. Please make sure the backend is running.</div>;
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex justify-between items-end">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-white drop-shadow-md">Portfolio Dashboard</h1>
          <p className="text-slate-400 mt-1 text-sm font-medium">Real-time asset allocation overview & analytics.</p>
        </div>
        {data.is_mock && (
          <span className="px-3 py-1 bg-[#FF3366]/20 text-[#FF3366] text-xs font-bold tracking-widest rounded-full border border-[#FF3366]/50 shadow-[0_0_10px_rgba(255,51,102,0.3)]">
            MOCK MODE
          </span>
        )}
      </div>

      {/* Asset Overview Cards */}
      <SummaryCards data={data} />

      {/* Analytics Charts & Timeline */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          {/* Charts Area */}
          <div className="glass-card p-6">
             <div className="flex justify-between items-center mb-6">
                <h3 className="text-lg font-semibold text-white">Asset Growth & Timeline Analysis</h3>
                <div className="flex bg-[#1A1A1A] p-1 rounded-lg border border-white/5">
                   <button 
                     onClick={() => setTimeframe("daily")}
                     className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all duration-300 ${timeframe === "daily" ? "bg-white/10 text-[#B2FF05] shadow-lg" : "text-slate-400 hover:text-white"}`}>Daily</button>
                   <button 
                     onClick={() => setTimeframe("monthly")}
                     className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all duration-300 ${timeframe === "monthly" ? "bg-white/10 text-[#B2FF05] shadow-lg" : "text-slate-400 hover:text-white"}`}>Monthly</button>
                   <button 
                     onClick={() => setTimeframe("intraday")}
                     className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-all duration-300 ${timeframe === "intraday" ? "bg-white/10 text-[#00E5FF] shadow-lg" : "text-slate-400 hover:text-white"}`}>Intraday (VWAP)</button>
                </div>
             </div>
             <div className="relative">
               {chartLoading && (
                 <div className="absolute inset-0 z-10 flex items-center justify-center bg-[#0E0E0E]/50 backdrop-blur-sm rounded-xl">
                   <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-[#B2FF05]"></div>
                 </div>
               )}
               <PortfolioChart data={chartData} timeframe={timeframe} />
             </div>
          </div>

          {/* Positions Table */}
          <PositionsTable positions={data.positions || []} />
        </div>

        {/* Sidebar: Activity & Rules */}
        <ActivityLog activities={data.recent_activity || []} />
      </div>
    </div>
  );
}
