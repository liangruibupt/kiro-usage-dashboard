import { Navigate, Route, Routes } from "react-router-dom";
import { isAuthenticated } from "./auth/auth";
import { LoginPage } from "./pages/LoginPage";
import { CallbackPage } from "./pages/CallbackPage";
import { DashboardPage } from "./pages/DashboardPage";
import { MonthlyReportPage } from "./pages/MonthlyReportPage";
import { AdminManagementPage } from "./pages/AdminManagementPage";
import { TierRequestsPage } from "./pages/TierRequestsPage";

function RequireAuth({ children }: { children: JSX.Element }) {
  return isAuthenticated() ? children : <Navigate to="/login" replace />;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/callback" element={<CallbackPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <DashboardPage />
          </RequireAuth>
        }
      />
      <Route
        path="/report"
        element={
          <RequireAuth>
            <MonthlyReportPage />
          </RequireAuth>
        }
      />
      <Route
        path="/admins"
        element={
          <RequireAuth>
            <AdminManagementPage />
          </RequireAuth>
        }
      />
      <Route
        path="/tier-requests"
        element={
          <RequireAuth>
            <TierRequestsPage />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
