const voiceIcon = document.getElementById("voice-icon");
const windowModeToggle = document.getElementById("window-mode-toggle");
let windowMode = "floating";
let keyPressed = false;

const setListening = (listening) => {
  voiceIcon.classList.toggle("visible", listening);
  voiceIcon.classList.toggle("hidden", !listening);
  document.body.classList.toggle("jarvis-listening", listening);
};

window.setListening = setListening;

const applyWindowMode = (mode) => {
  windowMode = mode;
  document.body.classList.toggle("mode-floating", mode === "floating");
  document.body.classList.toggle("mode-original", mode === "original");

  const originalMode = mode === "original";
  windowModeToggle.setAttribute(
    "aria-label",
    originalMode ? "Voltar ao modo flutuante" : "Voltar à janela original"
  );
  windowModeToggle.title = originalMode
    ? "Voltar ao modo flutuante"
    : "Voltar à janela original";
  windowModeToggle.querySelector("span").textContent = originalMode ? "◒" : "↗";
  window.setWaveformActive?.(originalMode);
};

windowModeToggle.addEventListener("pointerdown", (event) => {
  event.stopPropagation();
});

windowModeToggle.addEventListener("click", async (event) => {
  event.stopPropagation();
  const nextMode = windowMode === "floating" ? "original" : "floating";
  windowModeToggle.disabled = true;
  try {
    if (!window.pywebview?.api?.set_window_mode) {
      throw new Error("A ponte com a janela ainda não está disponível.");
    }
    await window.pywebview.api.set_window_mode(nextMode);
    applyWindowMode(nextMode);
  } catch (error) {
    console.error("Não foi possível alterar o modo da janela", error);
  } finally {
    windowModeToggle.disabled = false;
  }
});

document.addEventListener("keydown", function (event) {
  if (event.code === "Space" && !keyPressed) {
    keyPressed = true;
    setListening(true);
  }
});
document.addEventListener("keyup", function (event) {
  if (event.code === "Space") {
    keyPressed = false;
    setListening(false);
  }
});

const displayLoader = (shouldDisplay) => {
  document.body.classList.toggle("jarvis-thinking", shouldDisplay);
  // remove existing loaders
  const elements = document.querySelectorAll(`.loader`);
  if (shouldDisplay && elements.length == 0) {
    const c = document.querySelector(".arc_reactor_container");
    loader = document.createElement("span");
    loader.classList.add("loader");
    c.appendChild(loader);
  } else {
    elements.forEach((element) => {
      element.remove();
    });
  }
};

const displayLine = (isUser, message) => {
  const chatList = document.querySelector(".chat_list");

  // Create the list item
  const listItem = document.createElement("li");
  listItem.classList.add("chat_list_item");

  // Create the keyword (USER or JARVIS)
  const keyword = document.createElement("p");
  keyword.classList.add(isUser ? "keyword-user" : "keyword-jarvis");
  keyword.textContent = isUser ? "USER: " : "JARVIS: ";

  // Create the message content
  const normalWord = document.createElement("p");
  normalWord.classList.add("normal_word");
  normalWord.textContent = message;

  // Append keyword and message to the list item
  listItem.appendChild(keyword);
  listItem.appendChild(normalWord);

  // Append the list item to the chat list
  chatList.appendChild(listItem);

  // Check if there are more than 6 items in the list
  if (chatList.children.length > 6) {
    // Remove the first item (FIFO - First In, First Out)
    chatList.removeChild(chatList.children[0]);
  }

  if (!isUser && windowMode === "floating") {
    document.body.classList.add("jarvis-thinking");
    window.setTimeout(() => document.body.classList.remove("jarvis-thinking"), 900);
  }
};
