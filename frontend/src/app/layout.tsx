import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Link from "next/link";
import StockSearch from "@/components/StockSearch";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Project Big Tester - AI Asset Management",
  description: "AI-driven wealth management platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ja" className="dark">
      <body className={`${inter.className} bg-[var(--background)] text-[var(--foreground)] min-h-screen flex flex-col`}>
        {/* Navigation Bar (Glassmorphism) */}
        <nav className="sticky top-0 z-50 glass-panel border-x-0 border-t-0 rounded-none bg-[#0E0E0E]/80 border-b border-white/5">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div className="flex items-center justify-between h-16">
              <div className="flex items-center gap-8">
                <span className="text-xl font-bold bg-gradient-to-r from-[#B2FF05] to-[#00E5FF] bg-clip-text text-transparent drop-shadow-[0_0_8px_rgba(178,255,5,0.3)]">
                  Project Big Tester
                </span>
                <div className="flex gap-4">
                  <Link href="/" className="px-3 py-2 rounded-md text-sm font-medium hover:bg-white/5 hover:text-[#B2FF05] transition-all duration-300">
                    Dashboard
                  </Link>
                  <Link href="/reports" className="px-3 py-2 rounded-md text-sm font-medium hover:bg-white/5 hover:text-[#B2FF05] transition-all duration-300">
                    AI Reports
                  </Link>
                </div>
              </div>
              <div className="flex items-center">
                <StockSearch />
              </div>
            </div>
          </div>
        </nav>
        
        {/* Main Content */}
        <main className="flex-1 w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
          {children}
        </main>
      </body>
    </html>
  );
}
