"use client";

import { useEffect, useState } from "react";

interface AIReport {
  report_id: string;
  report_type: string;
  target_date: string;
  file_path: string;
  ai_summary: string;
}

export default function ReportsAlbum() {
  const [reports, setReports] = useState<AIReport[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("http://localhost:8000/api/v1/reports")
      .then((res) => res.json())
      .then((json) => {
        setReports(json);
        setLoading(false);
      })
      .catch((err) => {
        console.error("Failed to fetch reports:", err);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return <div className="flex items-center justify-center h-64"><div className="animate-pulse text-slate-400">Loading AI Reports...</div></div>;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-slate-100">AI Report Album</h1>
        <p className="text-slate-400 mt-1">Review your past performance and AI-driven insights.</p>
      </div>

      <div className="grid grid-cols-1 gap-6">
        {reports.map((report) => (
          <div key={report.report_id} className="p-6 rounded-xl bg-slate-900/40 border border-slate-800/60 hover:bg-slate-800/40 transition-colors backdrop-blur-sm group">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-3">
                <span className={`px-3 py-1 rounded-full text-xs font-semibold ${
                  report.report_type === 'Daily' ? 'bg-blue-500/20 text-blue-300 border border-blue-500/30' : 
                  report.report_type === 'Monthly' ? 'bg-purple-500/20 text-purple-300 border border-purple-500/30' : 
                  'bg-amber-500/20 text-amber-300 border border-amber-500/30'
                }`}>
                  {report.report_type}
                </span>
                <span className="text-sm text-slate-400">
                  {new Date(report.target_date).toLocaleDateString('ja-JP', { year: 'numeric', month: 'long', day: 'numeric' })}
                </span>
              </div>
              <button className="opacity-0 group-hover:opacity-100 transition-opacity text-indigo-400 text-sm hover:text-indigo-300">
                View Full Details &rarr;
              </button>
            </div>
            
            <div className="p-4 rounded-lg bg-slate-950/50 border border-slate-800/50">
              <h4 className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-2">AI Summary & If-Then Analysis</h4>
              <p className="text-slate-300 leading-relaxed text-sm">
                {report.ai_summary}
              </p>
            </div>
          </div>
        ))}

        {reports.length === 0 && (
          <div className="text-center py-12 text-slate-500">
            No reports available yet.
          </div>
        )}
      </div>
    </div>
  );
}
