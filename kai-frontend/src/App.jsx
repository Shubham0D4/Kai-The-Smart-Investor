import React, { useEffect, useState } from 'react';
import { BrowserRouter as Router, Link, Navigate, Route, Routes } from 'react-router-dom';
import { Moon, Sun } from 'lucide-react';
import { CommitteeProvider } from './context/CommitteeContext';
import ToolCallIndicator from './components/ToolCallIndicator';
import MandateModal from './features/mandates/MandateModal';
import Dashboard from './pages/Dashboard';
import TickerPage from './pages/TickerPage';
import ChatWidget from './components/ChatWidget';
import Chip from './components/Chip';

// Light is the default; the choice is remembered when localStorage allows it.
const readTheme = () => {
  try {
    return window.localStorage.getItem('kai_theme') === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
};

const Logo = () => (
  <svg width="18" height="18" viewBox="0 0 32 32" fill="none" aria-hidden="true">
    <path d="M10 7v18M10 16.5l9.5-9M12.8 15 21 25" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

function AppContent() {
  const [theme, setTheme] = useState(readTheme);

  useEffect(() => {
    // data-bs-theme themes Bootstrap's components; data-theme is kept for our own selectors.
    document.documentElement.setAttribute('data-bs-theme', theme);
    document.documentElement.setAttribute('data-theme', theme);
    try {
      window.localStorage.setItem('kai_theme', theme);
    } catch {
      /* the theme just will not persist */
    }
  }, [theme]);

  return (
    <>
      <header className="kai-header">
        <div className="kai-container kai-header-inner">
          <Link to="/" className="kai-brand">
            <span className="kai-brand-mark"><Logo /></span>
            <span>
              <div className="kai-brand-name">Kai</div>
              <div className="kai-brand-tag">Explainable investing copilot</div>
            </span>
          </Link>
          <div className="d-flex align-items-center gap-3">
            <Chip tone="neutral" className="d-none d-sm-inline-flex">Local · single user</Chip>
            <button
              onClick={() => setTheme((t) => (t === 'light' ? 'dark' : 'light'))}
              aria-label={theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme'}
              className="kai-iconbtn"
            >
              {theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}
            </button>
          </div>
        </div>
      </header>

      <main className="kai-main">
        <div className="kai-container">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/ticker/:symbol" element={<TickerPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </div>
      </main>

      <footer className="kai-footer">
        <div className="kai-container">
          Kai · research tool, not investment advice · figures come from SEC filings, news feeds and market data · &copy; {new Date().getFullYear()}
        </div>
      </footer>
      <ToolCallIndicator />
      <MandateModal />
      <ChatWidget />
    </>
  );
}

function App() {
  return (
    <Router>
      <CommitteeProvider>
        <AppContent />
      </CommitteeProvider>
    </Router>
  );
}

export default App;
