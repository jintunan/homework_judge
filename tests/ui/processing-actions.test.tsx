import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ProcessingPage } from "../../client/src/features/processing/ProcessingPage";
import { api } from "../../client/src/lib/api";

vi.mock("../../client/src/lib/api", () => ({api: vi.fn()}));

function renderPage() {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}, mutations: {retry: false}}});
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/tasks/task/processing"]}>
        <Routes><Route path="/tasks/:taskId/processing" element={<ProcessingPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("processing actions", () => {
  beforeEach(() => vi.mocked(api).mockReset());

  it("shows a retry error and restores the button", async () => {
    vi.mocked(api).mockImplementation(async (path, init) => {
      if (init?.method === "POST") throw new Error("服务暂时不可用");
      return {status: "failed", errorCode: "FAILED", errorMessage: "识别失败"} as never;
    });
    renderPage();
    const retry = await screen.findByRole("button", {name: "重新运行"});
    fireEvent.click(retry);
    expect(await screen.findByRole("alert")).toHaveTextContent("服务暂时不可用");
    await waitFor(() => expect(retry).toBeEnabled());
  });
});
