// Chat state with React's own useReducer - no state library for one widget.

export type Role = "user" | "bot" | "error";

export type Message = { id: number; role: Role; text: string; route?: string };

export type ChatState = { messages: Message[]; sending: boolean; nextId: number };

export type ChatAction =
  | { type: "send"; text: string }
  | { type: "reply"; text: string; route: string }
  | { type: "fail"; text: string };

export const initialState: ChatState = { messages: [], sending: false, nextId: 1 };

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case "send":
      return {
        messages: [...state.messages, { id: state.nextId, role: "user", text: action.text }],
        sending: true,
        nextId: state.nextId + 1,
      };
    case "reply":
      return {
        // a bot-side failure (route "error:...") already carries its own cause-specific sentence
        messages: [...state.messages, {
          id: state.nextId,
          role: action.route.startsWith("error:") ? "error" : "bot",
          text: action.text,
          route: action.route,
        }],
        sending: false,
        nextId: state.nextId + 1,
      };
    case "fail":
      return {
        messages: [...state.messages, { id: state.nextId, role: "error", text: action.text }],
        sending: false,
        nextId: state.nextId + 1,
      };
  }
}
