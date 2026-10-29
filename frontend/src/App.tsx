import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useEffect } from "react";
import { RedirectIfSignedIn, RequireRole, SessionExpiryWatcher } from "@/auth/AuthContext";
import ResetPassword from "@/pages/auth/ResetPassword";
import SignIn from "@/pages/auth/SignIn";
import AddCustomer from "@/pages/bd/AddCustomer";
import BdDashboard from "@/pages/bd/BdDashboard";
import CustomerPreview from "@/pages/bd/CustomerPreview";
import CustomerBilling from "@/pages/customer/CustomerBilling";
import CustomerHome from "@/pages/customer/CustomerHome";
import { paths } from "@/routes";

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

export default function App() {
  return (
    <>
      <ScrollToTop />
      <SessionExpiryWatcher />
      <Routes>
        <Route path={paths.signIn} element={<RedirectIfSignedIn><SignIn /></RedirectIfSignedIn>} />
        <Route path={paths.resetPassword} element={<RedirectIfSignedIn><ResetPassword /></RedirectIfSignedIn>} />
        <Route path={paths.customerDashboard} element={<RequireRole role="customer"><CustomerHome /></RequireRole>} />
        <Route path={paths.customerBilling} element={<RequireRole role="customer"><CustomerBilling /></RequireRole>} />
        <Route path={paths.bdDashboard} element={<RequireRole role="bd"><BdDashboard /></RequireRole>} />
        <Route path={paths.bdAddCustomer} element={<RequireRole role="bd"><AddCustomer /></RequireRole>} />
        <Route path={paths.bdCustomer()} element={<RequireRole role="bd"><CustomerPreview /></RequireRole>} />
        <Route path="*" element={<Navigate to={paths.signIn} replace />} />
      </Routes>
    </>
  );
}
