import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {fireEvent, render, screen, waitFor} from "@testing-library/react";
import {MemoryRouter, Route, Routes} from "react-router-dom";
import {beforeEach, describe, expect, it, vi} from "vitest";

import type {StudentSubmissionSummary} from "@shared/contracts";
import {StudentSubmissionsPage} from "@/features/students/StudentSubmissionsPage";
import {api, deleteStudentSubmission} from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: vi.fn(),
  uploadStudentSubmission: vi.fn(),
  deleteStudentSubmission: vi.fn()
}));

const submissions: StudentSubmissionSummary[] = [
  {
    id: "submission-1", task_id: "task", student_identifier: "S1", student_name: "学生一",
    original_name: "one.pdf", page_count: 1, status: "ready", error_code: null,
    error_message: null, question_region_status: "ready", question_region_error_code: null,
    question_region_error_message: null, created_at: "2026-01-01", updated_at: "2026-01-01"
  },
  {
    id: "submission-2", task_id: "task", student_identifier: "S2", student_name: "学生二",
    original_name: "two.pdf", page_count: 1, status: "ready", error_code: null,
    error_message: null, question_region_status: "ready", question_region_error_code: null,
    question_region_error_message: null, created_at: "2026-01-02", updated_at: "2026-01-02"
  }
];

function renderPage() {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}, mutations: {retry: false}}});
  return render(<QueryClientProvider client={client}>
    <MemoryRouter initialEntries={["/tasks/task/students"]}>
      <Routes><Route path="/tasks/:taskId/students" element={<StudentSubmissionsPage />} /></Routes>
    </MemoryRouter>
  </QueryClientProvider>);
}

describe("student submission deletion", () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(deleteStudentSubmission).mockReset();
    vi.mocked(api).mockImplementation(async (path) => {
      if (path === "/tasks/task/student-submissions") return submissions as never;
      if (path === "/tasks/task/review") return {pages: [], studentUploadGate: {ready: true}} as never;
      if (path.startsWith("/student-submissions/")) return new Promise(() => {}) as never;
      throw new Error(`Unexpected path ${path}`);
    });
  });

  it("requires confirmation and cancel does not delete", async () => {
    renderPage();
    fireEvent.click(await screen.findByRole("button", {name: "删除学生答卷 学生一"}));
    expect(screen.getByRole("dialog", {name: "永久删除这份学生答卷？"})).toHaveTextContent("学生一");
    expect(screen.getByText(/任务模板和其他学生答卷不会受影响/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", {name: "取消"}));
    expect(deleteStudentSubmission).not.toHaveBeenCalled();
  });

  it("deletes the selected submission after confirmation", async () => {
    vi.mocked(deleteStudentSubmission).mockResolvedValue({
      submissionId: "submission-1", taskId: "task", deleted: true,
      cancelledJobs: 0, cleanupPending: false
    });
    renderPage();
    fireEvent.click(await screen.findByRole("button", {name: "删除学生答卷 学生一"}));
    fireEvent.click(screen.getByRole("button", {name: "永久删除这份答卷"}));
    await waitFor(() => expect(deleteStudentSubmission).toHaveBeenCalledWith("submission-1"));
    expect(await screen.findByText("学生答卷已永久删除")).toBeInTheDocument();
  });
});
