type FeedbackTone = "status" | "error" | "disabled";

function FeedbackLine({tone, children}: {tone: FeedbackTone; children: string}) {
  const role = tone === "error" ? "alert" : "status";
  return <p className={`action-feedback action-feedback--${tone}`} role={role}>{children}</p>;
}

export function ActionFeedback({
  message,
  error,
  disabledReason
}: {
  message?: string;
  error?: string;
  disabledReason?: string;
}) {
  return (
    <>
      {error ? <FeedbackLine tone="error">{error}</FeedbackLine> : null}
      {!error && message ? <FeedbackLine tone="status">{message}</FeedbackLine> : null}
      {!error && !message && disabledReason ? <FeedbackLine tone="disabled">{disabledReason}</FeedbackLine> : null}
    </>
  );
}
