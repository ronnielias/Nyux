const button = document.querySelector("#send");
const result = document.querySelector("#result");

button.addEventListener("click", async () => {
  result.textContent = "Enviando...";
  try {
    const response = await fetch("/api/echo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: "Hola desde JavaScript" }),
    });
    result.textContent = JSON.stringify(await response.json(), null, 2);
  } catch (error) {
    result.textContent = `Error: ${error.message}`;
  }
});
