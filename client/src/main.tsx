import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "@/app/AppShell";
import { CreateTaskPage } from "@/features/tasks/CreateTaskPage";
import { TaskListPage } from "@/features/tasks/TaskListPage";
import { ProcessingPage } from "@/features/processing/ProcessingPage";
import { ReviewPage } from "@/features/review/ReviewPage";
import { StudentSubmissionsPage } from "@/features/students/StudentSubmissionsPage";
import { GradingWorkspacePage } from "@/features/grading/GradingWorkspacePage";
import "katex/dist/katex.min.css";
import "mathlive/fonts.css";
import "@/styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {retry: 1, staleTime: 5_000}
  }
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<TaskListPage />} />
            <Route path="/new" element={<CreateTaskPage />} />
            <Route path="/tasks/:taskId/processing" element={<ProcessingPage />} />
            <Route path="/tasks/:taskId/review" element={<ReviewPage />} />
            <Route path="/tasks/:taskId/students" element={<StudentSubmissionsPage />} />
            <Route path="/tasks/:taskId/students/:submissionId/grading" element={<GradingWorkspacePage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
