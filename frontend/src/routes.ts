/** Every URL in the app, so pages never hand-build paths. */
export const paths = {
  signIn: "/signin",
  resetPassword: "/reset-password",
  customerDashboard: "/customer",
  customerBilling: "/customer/billing",
  bdDashboard: "/bd",
  bdAddCustomer: "/bd/customers/new",
  bdCustomer: (id = ":id") => `/bd/customers/${id}`,
} as const;
