
export const Notify = async ({ $ }) => {
  return {
    event: async ({ event }) => {
      if (event.type === "session.idle") {
        // Linux/Manjaro: notify-send
        await $`notify-send "opencode" "Агент закончил задачу"`.nothrow()
      }
    },
  }
}
