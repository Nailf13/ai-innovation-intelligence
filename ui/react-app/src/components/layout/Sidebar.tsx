import { NavLink } from 'react-router-dom';
import {
  Mic,
  FileText,
  PlayCircle,
  BarChart3,
  Settings,
  Sparkles,
} from 'lucide-react';
import clsx from 'clsx';

const navItems = [
  { to: '/insights', label: 'Insights', icon: Sparkles },
  { to: '/podcasts', label: 'Podcasts', icon: Mic },
  { to: '/documents', label: 'Documents', icon: FileText },
  { to: '/pipelines', label: 'Pipelines', icon: PlayCircle },
];

export function Sidebar() {
  return (
    <aside className="w-64 bg-white border-r border-gray-200 flex flex-col h-screen">
      {/* Logo */}
      <div className="h-16 flex items-center px-6 border-b border-gray-200">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-savencia-primary rounded-lg flex items-center justify-center">
            <BarChart3 className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="font-semibold text-gray-900 text-sm">Innovation</h1>
            <p className="text-xs text-gray-500">Intelligence</p>
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-4 py-6 space-y-1">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              clsx(
                'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
                isActive
                  ? 'bg-savencia-primary text-white'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              )
            }
          >
            <Icon className="w-5 h-5" />
            {label}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="p-4 border-t border-gray-200">
        <NavLink
          to="/settings"
          className={({ isActive }) =>
            clsx(
              'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
              isActive
                ? 'bg-savencia-primary text-white'
                : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
            )
          }
        >
          <Settings className="w-5 h-5" />
          Settings
        </NavLink>
        <div className="mt-4 px-3 text-xs text-gray-400">
          Savencia Health Trends
          <br />
          v1.0.0
        </div>
      </div>
    </aside>
  );
}
