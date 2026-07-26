import { AlertCircle, CheckCircle2, X } from "lucide-react";

export interface FeedbackMessage {
  type: "success" | "error";
  text: string;
}

export function Feedback({
  message,
  onDismiss,
}: {
  message: FeedbackMessage | null;
  onDismiss: () => void;
}) {
  if (!message) return null;
  const Icon = message.type === "success" ? CheckCircle2 : AlertCircle;
  return (
    <div className={`feedback feedback-${message.type}`} role="status">
      <Icon size={18} />
      <span>{message.text}</span>
      <button type="button" onClick={onDismiss} aria-label="关闭提示">
        <X size={16} />
      </button>
    </div>
  );
}
