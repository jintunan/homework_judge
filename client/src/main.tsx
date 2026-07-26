import React from "react";
import ReactDOM from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
} from "react-router-dom";
import { AppShell } from "@client/app/AppShell";
import { DashboardPage } from "@client/features/dashboard/DashboardPage";
import {
  CreateTaskPage,
  TaskSetupPage,
} from "@client/features/tasks/CreateTaskPage";
import { UploadPage } from "@client/features/submissions/UploadPage";
import { ReviewPage } from "@client/features/review/ReviewPage";
import { ReportsPage } from "@client/features/reports/ReportsPage";
import { AnswerConfigPage } from "@client/features/answer-config/AnswerConfigPage";
import "@client/styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <DashboardPage /> },
      { path: "tasks/new", element: <CreateTaskPage /> },
      { path: "tasks/:taskId/setup", element: <TaskSetupPage /> },
      { path: "tasks/:taskId/answers", element: <AnswerConfigPage /> },
      { path: "tasks/:taskId/upload", element: <UploadPage /> },
      { path: "tasks/:taskId/review", element: <ReviewPage /> },
      {
        path: "tasks/:taskId/review/:submissionId",
        element: <ReviewPage />,
      },
      { path: "tasks/:taskId/reports", element: <ReportsPage /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </React.StrictMode>,
);
