// Copy that names the guest's assistant. Kept in one place so #71 (any AI assistant, not only
// Claude) changes it here only.

export const ASSISTANT = "Claude";

export const strings = {
  profileLive: `Live — discoverable from ${ASSISTANT}`,
  profileHidden: "Hidden from guests until you publish",
  viaAssistant: ASSISTANT,
  rulesSaved: `${ASSISTANT} reads the new rules now.`,
  readsAboutYou: `What ${ASSISTANT} reads about you`,
} as const;
