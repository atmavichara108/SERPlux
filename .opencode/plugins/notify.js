
export default async ({ $ }) => {
  const SOUND_COMPLETE = "/usr/share/sounds/freedesktop/stereo/complete.oga"
  const SOUND_ERROR = "/usr/share/sounds/freedesktop/stereo/dialog-error.oga"

  /** Fire paplay and forget. Never throws, never blocks. */
  const playSound = (soundPath) => {
    try {
      $`paplay ${soundPath}`.nothrow()
    } catch {
      // Audio failed silently — do not crash the session
    }
  }

  return {
    event: async (input) => {
      const { event } = input

      if (event.type === "session.idle") {
        playSound(SOUND_COMPLETE)
      }

      if (
        event.type?.includes("error") ||
        event.type?.includes("fail") ||
        (event.type === "task.done" && event.status === "failed")
      ) {
        playSound(SOUND_ERROR)
      }
    },
  }
}
