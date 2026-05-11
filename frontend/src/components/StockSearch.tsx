"use client";
import { useState } from "react";

export default function StockSearch() {
  const [query, setQuery] = useState("");
  const [isFocused, setIsFocused] = useState(false);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query) return;
    alert(`Searching for ticker: ${query}\n(Stock Details view will be implemented in future update)`);
    setQuery("");
  };

  return (
    <form onSubmit={handleSearch} className="relative hidden md:block">
      <div className={`flex items-center transition-all duration-300 ${isFocused ? 'w-64' : 'w-48'}`}>
        <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
          <svg className={`h-4 w-4 ${isFocused ? 'text-[#00E5FF]' : 'text-slate-500'} transition-colors`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </div>
        <input
          type="text"
          className="block w-full pl-10 pr-3 py-1.5 border border-white/10 rounded-full leading-5 bg-[#1A1A1A]/80 text-slate-300 placeholder-slate-500 focus:outline-none focus:ring-1 focus:ring-[#00E5FF] focus:border-[#00E5FF] sm:text-sm transition-all"
          placeholder="Search Ticker (e.g. 7203)"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
        />
      </div>
    </form>
  );
}
