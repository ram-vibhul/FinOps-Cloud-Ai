/** Plain-English messages for sign-in error codes returned by the server. */
const MESSAGES: Record<string, string> = {
  access_denied: "Sign-in was cancelled.",
  state_expired: "Sign-in took too long or was started in another tab. Please try again.",
  state_mismatch: "Sign-in could not be verified. Please try again.",
  invalid_token: "The sign-in provider sent a response we could not verify. Please try again.",
  code_exchange_failed: "The sign-in provider rejected the request. Please try again.",
  provider_unavailable: "The sign-in provider is not responding. Try again in a few minutes.",
  provider_misconfigured: "This sign-in option is not set up correctly. Ask your administrator.",
  provider_error: "The sign-in provider reported an error. Please try again.",
  email_missing: "Your sign-in provider did not share an email address.",
  email_not_verified:
    "Your sign-in provider has not confirmed that you own this email address, so it can't be linked to the existing account. Sign in the way you did before.",
  domain_not_allowed: "Accounts with your email domain cannot sign in to this installation.",
  account_disabled: "This account has been disabled.",
};

export function signInErrorMessage(code: string | null): string | null {
  if (!code) return null;
  return MESSAGES[code] ?? "Sign-in failed. Please try again.";
}
