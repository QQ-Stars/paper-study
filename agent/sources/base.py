from abc import ABC, abstractmethod


class Source(ABC):
    name = "base"

    @abstractmethod
    def search(self, query, years, limit):
        """返回 PaperStub 可迭代对象。"""
        ...
