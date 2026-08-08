import type { Metadata } from 'next';
import Link from 'next/link';
import './globals.css';

export const metadata: Metadata = {
  title: 'Papercraft',
  description: 'A living knowledge graph of papers, methods, datasets and claims.',
};

const NAV = [
  { href: '/', label: 'Ingest' },
  { href: '/ask', label: 'Ask' },
  { href: '/curate', label: 'Review queue' },
];

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="mx-auto max-w-4xl px-6 py-8">
          <header className="mb-8 flex flex-wrap items-baseline gap-x-6 gap-y-2 border-b border-edge pb-4">
            <Link href="/" className="text-lg font-semibold text-slate-100">
              Papercraft
            </Link>
            <nav className="flex gap-4 text-sm">
              {NAV.map((item) => (
                <Link key={item.href} href={item.href} className="text-muted hover:text-accent">
                  {item.label}
                </Link>
              ))}
            </nav>
          </header>
          <main className="space-y-6">{children}</main>
        </div>
      </body>
    </html>
  );
}
