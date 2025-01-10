// 更新时钟的函数
function updateClock() {
    const now = new Date();
    const timeString = now.toLocaleTimeString();
    const dateString = now.toLocaleDateString();
    document.getElementById('clock').textContent = `${dateString} ${timeString}`;
}

// 每秒更新时钟
setInterval(updateClock, 1000);
updateClock(); // 初始化时钟

// 存储留言的数组
let messages = JSON.parse(localStorage.getItem('messages')) || [];

// 留言类
class Message {
    constructor(content, image = null) {
        this.id = Date.now();
        this.content = content;
        this.image = image;
        this.timestamp = new Date();
    }
}

// 渲染留言
function renderMessages() {
    const container = document.getElementById('messagesContainer');
    container.innerHTML = messages.map(message => `
        <div class="message" data-id="${message.id}">
            <div class="message-header">
                <span>留言 #${message.id}</span>
                <span>${new Date(message.timestamp).toLocaleString()}</span>
            </div>
            <div class="message-content">${message.content}</div>
            ${message.image ? `<img src="${message.image}" class="message-image" alt="留言图片">` : ''}
        </div>
    `).join('');
}

// 添加留言
function addMessage(content, image = null) {
    const message = new Message(content, image);
    messages.unshift(message);
    localStorage.setItem('messages', JSON.stringify(messages));
    renderMessages();
}

// 处理图片上传
document.getElementById('imageUpload').addEventListener('change', function(e) {
    const file = e.target.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = function(e) {
            const imagePreview = e.target.result;
            document.getElementById('messageInput').dataset.image = imagePreview;
        };
        reader.readAsDataURL(file);
    }
});

// 提交留言
document.getElementById('submitBtn').addEventListener('click', function() {
    const input = document.getElementById('messageInput');
    const content = input.value.trim();
    const image = input.dataset.image;

    if (content || image) {
        addMessage(content, image);
        input.value = '';
        delete input.dataset.image;
    }
});

// 初始化渲染
renderMessages();

// 添加表情选择功能
document.getElementById('emojiBtn').addEventListener('click', function() {
    const emojis = ['😀', '😂', '🥰', '😎', '🤔', '👍', '❤️', '🌟'];
    const input = document.getElementById('messageInput');
    const currentPosition = input.selectionStart;
    
    const emojiContainer = document.createElement('div');
    emojiContainer.style.position = 'absolute';
    emojiContainer.style.background = 'white';
    emojiContainer.style.padding = '10px';
    emojiContainer.style.borderRadius = '8px';
    emojiContainer.style.boxShadow = '0 2px 4px rgba(0,0,0,0.2)';
    emojiContainer.style.display = 'flex';
    emojiContainer.style.gap = '5px';
    emojiContainer.style.flexWrap = 'wrap';

    emojis.forEach(emoji => {
        const span = document.createElement('span');
        span.textContent = emoji;
        span.style.cursor = 'pointer';
        span.style.fontSize = '20px';
        span.onclick = () => {
            const text = input.value;
            input.value = text.slice(0, currentPosition) + emoji + text.slice(currentPosition);
            emojiContainer.remove();
        };
        emojiContainer.appendChild(span);
    });

    const rect = this.getBoundingClientRect();
    emojiContainer.style.left = rect.left + 'px';
    emojiContainer.style.top = (rect.bottom + 5) + 'px';
    document.body.appendChild(emojiContainer);

    // 点击其他地方关闭表情选择器
    document.addEventListener('click', function closeEmoji(e) {
        if (!emojiContainer.contains(e.target) && e.target !== document.getElementById('emojiBtn')) {
            emojiContainer.remove();
            document.removeEventListener('click', closeEmoji);
        }
    });
}); 