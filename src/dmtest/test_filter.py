import re
from abc import ABC, abstractmethod


class TestFilter(ABC):
    @abstractmethod
    def matches(self, path, test, res_list):
        pass


class SubstringFilter(TestFilter):
    def __init__(self, substring):
        self.substring = substring

    def matches(self, path, test, res_list):
        return self.substring in path


class RegexFilter(TestFilter):
    def __init__(self, pattern):
        self.pattern = re.compile(pattern)

    def matches(self, path, test, res_list):
        return bool(self.pattern.search(path))


class StateFilter(TestFilter):
    def __init__(self, state):
        self.state = state.lower()

    def matches(self, path, test, res_list):
        if res_list == []:
            return self.state == "-"
        for result in res_list:
            if result.pass_fail.lower() == self.state:
                return True
        return False


class NotFilter(TestFilter):
    def __init__(self, sub_filter):
        self.sub_filter = sub_filter

    def matches(self, path, test, res_list):
        return not self.sub_filter.matches(path, test, res_list)


class CompositeFilter(TestFilter):
    def __init__(self):
        self.sub_filters = []

    def add_sub_filter(self, sub_filter):
        self.sub_filters.append(sub_filter)


class AndFilter(CompositeFilter):
    def matches(self, path, test, res_list):
        return all(
            sub_filter.matches(path, test, res_list) for sub_filter in self.sub_filters
        )


class OrFilter(CompositeFilter):
    def matches(self, path, test, res_list):
        if len(self.sub_filters) == 0:
            return True
        else:
            return any(
                sub_filter.matches(path, test, res_list) for sub_filter in self.sub_filters
            )
