import { useState } from "react";
import { Activity } from "./types";
import LossDrilldownModal from "./LossDrilldownModal";

export default function ActivityLog({ activities }: { activities: Activity[] }) {
  const [selectedActivity, setSelectedActivity] = useState<Activity | null>(null);

  return (
    <div className="space-y-6">
      {/* タイムライン */}
      <div className="glass-card p-6">
        <h3 className="text-lg font-semibold text-white mb-6 drop-shadow-md">AI Timeline & Events</h3>
        <div className="space-y-4">
          {(activities || []).map((act, idx) => (
            <div 
              key={idx} 
              className={`flex gap-3 text-sm group ${act.type === 'Trade' ? 'cursor-pointer' : ''}`}
              onClick={() => act.type === 'Trade' && setSelectedActivity(act)}
            >
              <div className="flex flex-col items-center">
                <div className={`w-2 h-2 rounded-full mt-1.5 shadow-[0_0_8px_currentColor] ${
                  act.type === 'Trade' && act.is_loss ? 'bg-[#FF3366] text-[#FF3366]' : 
                  act.type === 'Trade' ? 'bg-[#B2FF05] text-[#B2FF05]' : 
                  'bg-[#00E5FF] text-[#00E5FF]'
                }`} />
                {idx !== (activities || []).length - 1 && <div className="w-px h-full bg-white/10 my-1" />}
              </div>
              <div className="pb-4 flex-1">
                <div className="text-slate-500 text-xs mb-0.5">{new Date(act.timestamp).toLocaleString()}</div>
                <div className={`font-semibold ${act.type === 'Trade' ? 'text-white group-hover:text-[#B2FF05] transition-colors' : 'text-slate-300'}`}>
                  {act.title || act.type}
                </div>
                <div className="text-slate-400 mt-1">{act.description || act.message}</div>
                {act.type === 'Trade' && (
                  <div className="text-[10px] text-[#00E5FF] mt-1 uppercase tracking-wider opacity-0 group-hover:opacity-100 transition-opacity">
                    Click to view AI reasoning →
                  </div>
                )}
              </div>
            </div>
          ))}
          {activities.length === 0 && (
            <div className="text-slate-500 text-center py-4">No recent activity</div>
          )}
        </div>
      </div>

      {/* ルールステータス */}
      <div className="glass-panel p-6 border-l-[3px] border-l-[#B2FF05]">
        <h3 className="text-sm font-bold text-[#B2FF05] mb-3 flex items-center gap-2 uppercase tracking-widest">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
          System Core Status
        </h3>
        <ul className="text-sm text-slate-300 space-y-3">
          <li className="flex justify-between border-b border-white/5 pb-2"><span>Drawdown Lock</span> <span className="text-[#B2FF05] font-semibold drop-shadow-[0_0_5px_rgba(178,255,5,0.3)]">Active</span></li>
          <li className="flex justify-between border-b border-white/5 pb-2"><span>Overnight Risk</span> <span className="text-[#B2FF05] font-semibold drop-shadow-[0_0_5px_rgba(178,255,5,0.3)]">Cleared</span></li>
          <li className="flex justify-between"><span>Bank Reserve</span> <span className="text-[#B2FF05] font-semibold drop-shadow-[0_0_5px_rgba(178,255,5,0.3)]">Secure</span></li>
        </ul>
      </div>

      <LossDrilldownModal 
        activity={selectedActivity} 
        onClose={() => setSelectedActivity(null)} 
      />
    </div>
  );
}
