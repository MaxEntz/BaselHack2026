/** The conversation before its first message: the first question. */
export function Hello() {
  return (
    <div className="st-feed" role="log" aria-label="Conversation">
      <div className="st-hello">
        <h1>What's your website's address?</h1>
        <p>
          I'll read it and ask what it doesn't say. Then I'll build your agent site, which has
          no interface and answers agents directly, and your management site, in your own
          colours. Your public site doesn't change.
        </p>
      </div>
    </div>
  );
}
