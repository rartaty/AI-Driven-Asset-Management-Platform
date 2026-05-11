import { Activity } from "./types";

interface LossDrilldownModalProps {
  activity: Activity | null;
  onClose: () => void;
}

export default function LossDrilldownModal({ activity, onClose }: LossDrilldownModalProps) {
  if (!activity) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div 
        className="absolute inset-0 bg-[#0E0E0E]/80 backdrop-blur-sm"
        onClick={onClose}
      />
      
      {/* Modal */}
      <div className="relative w-full max-w-lg glass-card p-6 border-t-[3px] border-t-[#FF3366] shadow-[0_10px_40px_-10px_rgba(255,51,102,0.3)] transform transition-all">
        <button 
          onClick={onClose}
          className="absolute top-4 right-4 text-slate-400 hover:text-white transition-colors"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12"></path></svg>
        </button>

        <h2 className="text-xl font-bold text-white mb-1 flex items-center gap-2">
          <svg className="w-5 h-5 text-[#FF3366]" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
          Trade Drilldown Analysis
        </h2>
        <p className="text-[#FF3366] text-sm mb-6">{activity.title}</p>
        
        <div className="space-y-4">
          <div className="glass-panel p-4 bg-[#1A1A1A]/60">
            <h4 className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-1">Execution Details</h4>
            <p className="text-sm text-slate-300">{activity.description}</p>
            <p className="text-xs text-slate-500 mt-2">{new Date(activity.timestamp).toLocaleString()}</p>
          </div>
          
          <div className="glass-panel p-4 bg-[#1A1A1A]/60 border-l-[3px] border-l-[#00E5FF]">
            <h4 className="text-xs font-bold text-[#00E5FF] uppercase tracking-widest mb-2 flex items-center gap-2">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>
              AI Decision Reason
            </h4>
            <div className="text-sm text-slate-300 whitespace-pre-wrap leading-relaxed">
              {activity.reason || "No qualitative reason recorded for this trade."}
            </div>
          </div>
        </div>
        
        <div className="mt-6 flex justify-end">
          <button 
            onClick={onClose}
            className="px-4 py-2 bg-white/5 hover:bg-white/10 text-white text-sm font-medium rounded-lg transition-colors border border-white/10"
          >
            Close Analysis
          </button>
        </div>
      </div>
    </div>
  );
}
