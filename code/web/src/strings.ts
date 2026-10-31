// Copy that names the guest's assistant. Kept in one place so #71 (any AI assistant, not only
// Claude) changes it here only.

export const ASSISTANT = "Claude";

export const strings = {
  onboardingGreeting: `Hi! I'm your platform assistant. I'll turn your website into a booking agent that ${ASSISTANT} can talk to.\n\n**What's your restaurant's website?**`,
  profileLive: `Live — discoverable from ${ASSISTANT}`,
  viaAssistant: ASSISTANT,
} as const;
