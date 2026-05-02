document.addEventListener("DOMContentLoaded", function () {
    // === PHẦN 1: GIỮ NGUYÊN LOGIC CLICK FAQ CỦA BẠN ===
    const faqToggles = document.querySelectorAll('.faq-toggle');
    faqToggles.forEach(toggle => {
        toggle.addEventListener('click', function() {
            const item = this.parentElement;
            item.classList.toggle('active');

            // Nếu bạn dùng CSS để ẩn hiện (ví dụ display: block/none)
            const answer = this.nextElementSibling;
            if (answer.style.display === "block") {
                answer.style.display = "none";
            } else {
                answer.style.display = "block";
            }
        });
    });

    // === PHẦN 2: LOGIC MỞ/ĐÓNG BOX CHAT ===
    const openBtn = document.getElementById("openChatBtn");
    const closeBtn = document.getElementById("closeChatBtn");
    const widget = document.getElementById("chatWidget");

    if (openBtn) openBtn.onclick = () => widget.classList.remove("hidden");
    if (closeBtn) closeBtn.onclick = () => widget.classList.add("hidden");

    // === PHẦN 3: CHAT REALTIME (API THẬT) ===
    const chatForm = document.querySelector('[data-form-type="consultation-send"]');
    const chatThread = document.querySelector(".chat-thread");
    let lastMsgCount = 0;

    async function fetchMessages() {
        if (typeof roomId === 'undefined' || !roomId) return;
        try {
            const res = await fetch(`/api/chat/${roomId}/get/`);
            const data = await res.json();
            if (data.status === 'success' && data.messages.length !== lastMsgCount) {
                lastMsgCount = data.messages.length;
                chatThread.innerHTML = '';
                data.messages.forEach(msg => {
                    const isMe = msg.sender_id.toString() === userId.toString();
                    const row = document.createElement("div");
                    row.className = `chat-row ${isMe ? 'right' : 'left'}`;
                    row.innerHTML = `<p class="chat-bubble ${isMe ? 'solid' : 'outline'}">${msg.content}</p>`;
                    chatThread.appendChild(row);
                });
                chatThread.scrollTop = chatThread.scrollHeight;
            }
        } catch (e) { console.error(e); }
    }

    if (chatForm && !document.querySelector('.consultation-chat-shell')) {
    // Thêm điều kiện !document.querySelector('.consultation-chat-shell')
    // để nó KHÔNG chạy trên trang quản lý (trang quản lý có class này)
    chatForm.onsubmit = async (e) => {
        e.preventDefault();
            const input = chatForm.querySelector('input[name="message"]');
            const fd = new FormData();
            fd.append('room_id', roomId);
            fd.append('content', input.value);

            const res = await fetch('/api/chat/send/', {
                method: 'POST',
                headers: {'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value},
                body: fd
            });
            if (res.ok) {
                input.value = '';
                fetchMessages();
            }
        };
    }

    setInterval(fetchMessages, 2000);
    fetchMessages();
});