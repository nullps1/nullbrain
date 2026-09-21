package lab;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

// Proposed acceptance contract: review before committing/submitting.
class TextStatsAcceptanceTest {
    @Test void thresholdTwo() { assertEquals(3, TextStats.countWordsLongerThan("one two three", 2)); }
    @Test void thresholdThree() { assertEquals(1, TextStats.countWordsLongerThan("one two three", 3)); }
    @Test void thresholdFour() { assertEquals(1, TextStats.countWordsLongerThan("one two three", 4)); }
    @Test void thresholdFive() { assertEquals(0, TextStats.countWordsLongerThan("one two three", 5)); }
    @Test void empty() { assertEquals(0, TextStats.countWordsLongerThan("", 0)); }
    @Test void blank() { assertEquals(0, TextStats.countWordsLongerThan(" \t\n ", 0)); }
    @Test void zero() { assertEquals(3, TextStats.countWordsLongerThan("one two three", 0)); }
    @Test void nullInput() { assertThrows(IllegalArgumentException.class, () -> TextStats.countWordsLongerThan(null, 3)); }
    @Test void negativeThreshold() { assertThrows(IllegalArgumentException.class, () -> TextStats.countWordsLongerThan("one", -1)); }
    @Test void whitespace() { assertEquals(1, TextStats.countWordsLongerThan("\tone\n three  ", 3)); }
    @Test void equalBoundary() { assertEquals(1, TextStats.countWordsLongerThan("aa bbb cccc", 3)); }
    @Test void mixedLengths() { assertEquals(2, TextStats.countWordsLongerThan("a bb ccc dddd", 2)); }
}
