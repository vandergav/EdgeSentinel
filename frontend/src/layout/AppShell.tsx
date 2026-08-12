import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import "./AppShell.css";
import { useIncidentAttention } from "../lib/incidentAttention";
import { ChatPage } from "../pages/ChatPage";
import { EdgeSentinelBrand } from "./EdgeSentinalLogo";

interface NavItem {
  to: string;
  label: string;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/incidents", label: "Incidents" },
  { to: "/chat", label: "Chat" },
  { to: "/settings", label: "Settings" },
];

export function AppShell() {
  const { pathname } = useLocation();
  const isChatRoute = pathname === "/chat";
  const [hasOpenedChat, setHasOpenedChat] = useState(isChatRoute);
  const { attention } = useIncidentAttention();
  const unreadIncidentCount = attention?.unread_count ?? 0;

  useEffect(() => {
    if (isChatRoute) setHasOpenedChat(true);
  }, [isChatRoute]);

  return (
    <div className="app-shell">
      <aside className="app-shell__sidebar">
      <div className="app-shell__brand">
        <EdgeSentinelBrand />
      </div>

        <nav className="app-shell__nav" aria-label="Primary">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                "app-shell__nav-link" + (isActive ? " app-shell__nav-link--active" : "")
              }
            >
              <span>{item.label}</span>
              {item.to === "/incidents" && unreadIncidentCount > 0 && (
                <span className="app-shell__incident-badge" aria-label={`${unreadIncidentCount} unread incident updates`}>
                  {unreadIncidentCount > 99 ? "99+" : unreadIncidentCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="app-shell__main">
        {hasOpenedChat && (
          <div className={isChatRoute ? "app-shell__chat" : "app-shell__chat app-shell__chat--hidden"}>
            <ChatPage />
          </div>
        )}
        {!isChatRoute && <Outlet />}
      </main>
    </div>
  );
}
