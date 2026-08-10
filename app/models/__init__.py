"""ORM models package — importing it registers all models with Base.metadata."""

from app.models.chat import ChatConversation, ChatMessage, ChatMessageSender, ChatStatus
from app.models.customer import Address, CustomerProfile
from app.models.feedback import Feedback
from app.models.knowledge import KnowledgeArticle
from app.models.notification import Notification, NotificationType
from app.models.order import Order, OrderItem, OrderStatus
from app.models.product import Product
from app.models.refresh_token import RefreshToken
from app.models.ticket import Ticket, TicketComment, TicketPriority, TicketStatus
from app.models.user import User, UserRole

__all__ = [
    "Address",
    "ChatConversation",
    "ChatMessage",
    "ChatMessageSender",
    "ChatStatus",
    "CustomerProfile",
    "Feedback",
    "KnowledgeArticle",
    "Notification",
    "NotificationType",
    "Order",
    "OrderItem",
    "OrderStatus",
    "Product",
    "RefreshToken",
    "Ticket",
    "TicketComment",
    "TicketPriority",
    "TicketStatus",
    "User",
    "UserRole",
]
