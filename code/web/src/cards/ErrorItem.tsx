type Props = {
  message: string;
  // Present only on the latest error, when nothing else is running.
  onRetry?: () => void;
};

export function ErrorItem({ message, onRetry }: Props) {
  return (
    <div className="error-item" role="alert">
      <p>{message}</p>
      {onRetry ? (
        <button type="button" className="button-secondary" onClick={onRetry}>
          Try again
        </button>
      ) : (
        <p className="muted">Please try again.</p>
      )}
    </div>
  );
}
