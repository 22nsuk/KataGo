#include "../tests/tests.h"

#include "../core/test.h"
#include "../search/evalcache.h"
#include "../search/searchnode.h"

#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

namespace {

// Populate real SearchNodes directly: the cache reads their statistics and child
// pointers, and does not require a neural-net evaluation or a running Search.
struct CacheTestNode {
  SearchNode node;
  std::vector<std::unique_ptr<SearchNode>> children;

  CacheTestNode(Player pla, int64_t visits, double winLoss, double score)
    : node(pla,false,0,Hash128()), children()
  {
    setStats(node,visits,winLoss,score);
  }

  static void setStats(SearchNode& node, int64_t visits, double winLoss, double score) {
    node.stats.visits.store(visits);
    node.stats.winLossValueAvg.store(winLoss);
    node.stats.noResultValueAvg.store(0.125);
    node.stats.scoreMeanAvg.store(score);
    node.stats.leadAvg.store(score + 0.5);
    node.stats.utilityAvg.store(winLoss);
  }

  void addChild(Loc move, int64_t visits, double winLoss, double score, double utility) {
    testAssert(children.size() < SearchChildrenSizes::SIZE0TOTAL);
    if(children.empty()) {
      node.initializeChildren();
      node.state.store(SearchNode::STATE_EXPANDED0);
    }
    children.emplace_back(new SearchNode(getOpp(node.nextPla),false,0,Hash128()));
    SearchNode& child = *children.back();
    setStats(child,visits,winLoss,score);
    child.stats.utilityAvg.store(utility);
    SearchChildPointer& pointer = node.children0[children.size()-1];
    pointer.setMoveLoc(move);
    pointer.setEdgeVisits(visits);
    pointer.store(&child);
  }
};

void checkAggregate(const EvalCacheEntry& entry, const SearchNode& node) {
  testAssert(entry.cacheWeight == (float)node.stats.visits.load());
  testAssert(entry.avgWinLoss == (float)node.stats.winLossValueAvg.load());
  testAssert(entry.avgNoResult == (float)node.stats.noResultValueAvg.load());
  testAssert(entry.avgScoreMean == (float)node.stats.scoreMeanAvg.load());
  testAssert(entry.avgLead == (float)node.stats.leadAvg.load());
}

void checkHint(const FirstExploreEval& actual, const FirstExploreEval& expected) {
  testAssert(actual.cacheWeight == expected.cacheWeight);
  testAssert(actual.avgWinLoss == expected.avgWinLoss);
  testAssert(actual.avgScoreMean == expected.avgScoreMean);
}

void checkSnapshot(const EvalCacheEntry& actual, const EvalCacheEntry& expected) {
  testAssert(actual.cacheWeight == expected.cacheWeight);
  testAssert(actual.avgWinLoss == expected.avgWinLoss);
  testAssert(actual.avgNoResult == expected.avgNoResult);
  testAssert(actual.avgScoreMean == expected.avgScoreMean);
  testAssert(actual.avgLead == expected.avgLead);
  testAssert(actual.firstExploreEvals.size() == expected.firstExploreEvals.size());
  for(const auto& hint: expected.firstExploreEvals)
    checkHint(actual.firstExploreEvals.at(hint.first),hint.second);
}

void runSequentialCacheTests() {
  const Hash128 key(7,11);
  CacheTestNode high(P_WHITE,1000,0.75,12.0);
  for(int64_t visits: {749,750,800}) {
    EvalCacheTable cache(1);
    cache.update(key,&high.node,8,false,true);
    auto original = cache.find(key);
    testAssert(original != nullptr);
    const EvalCacheEntry snapshot = *original;
    CacheTestNode replacement(P_WHITE,visits,-0.5,-8.0);
    cache.update(key,&replacement.node,8,false,true);
    auto current = cache.find(key);
    testAssert(current != nullptr);
    if(visits < 750) {
      testAssert(current == original);
      checkAggregate(*current,high.node);
    }
    else {
      testAssert(current != original);
      checkAggregate(*current,replacement.node);
    }
    checkSnapshot(*original,snapshot);
  }

  const Loc moveA = Location::getLoc(3,3,9);
  const Loc moveB = Location::getLoc(5,5,9);
  {
    EvalCacheTable cache(1);
    cache.update(key,&high.node,8,false,true);
    auto original = cache.find(key);
    testAssert(original != nullptr);
    const EvalCacheEntry snapshot = *original;

    CacheTestNode first(P_WHITE,800,-0.5,-8.0);
    first.addChild(moveA,16,0.25,4.0,0.25);
    cache.update(key,&first.node,8,true,false);
    auto firstEntry = cache.find(key);
    testAssert(firstEntry != nullptr);
    checkAggregate(*firstEntry,high.node);
    testAssert(firstEntry->firstExploreEvals.size() == 1);
    checkHint(firstEntry->firstExploreEvals.at(moveA),FirstExploreEval(0.25f,4.0f,16.0f));
    const EvalCacheEntry firstSnapshot = *firstEntry;

    CacheTestNode weaker(P_WHITE,800,-0.5,-8.0);
    weaker.addChild(moveA,8,-0.25,-4.0,-0.25);
    weaker.addChild(moveB,7,-0.25,-4.0,-0.25);
    cache.update(key,&weaker.node,8,true,false);
    auto weakerEntry = cache.find(key);
    testAssert(weakerEntry != nullptr);
    checkSnapshot(*weakerEntry,firstSnapshot);

    CacheTestNode equal(P_WHITE,800,-0.5,-8.0);
    equal.addChild(moveA,16,-0.25,-4.0,-0.25);
    equal.addChild(moveB,8,0.5,8.0,0.5);
    cache.update(key,&equal.node,8,true,false);
    auto equalEntry = cache.find(key);
    testAssert(equalEntry != nullptr);
    checkAggregate(*equalEntry,high.node);
    testAssert(equalEntry->firstExploreEvals.size() == 2);
    checkHint(equalEntry->firstExploreEvals.at(moveA),FirstExploreEval(-0.25f,-4.0f,16.0f));
    checkHint(equalEntry->firstExploreEvals.at(moveB),FirstExploreEval(0.5f,8.0f,8.0f));
    checkSnapshot(*original,snapshot);
    checkSnapshot(*firstEntry,firstSnapshot);
  }

  // Root aggregates are suppressed independently by pass visit share or by
  // pass utility. Child hints are still recorded in either case.
  for(Player pla: {P_BLACK,P_WHITE}) {
    for(int passCase = 0; passCase < 3; passCase++) {
      EvalCacheTable cache(1);
      cache.update(key,&high.node,8,false,true);
      auto original = cache.find(key);
      testAssert(original != nullptr);
      const EvalCacheEntry snapshot = *original;
      CacheTestNode root(pla,900,-0.5,-8.0);
      const double sign = pla == P_WHITE ? 1.0 : -1.0;
      root.addChild(moveA,passCase == 0 ? 700 : 800,0.25,4.0,sign);
      root.addChild(Board::PASS_LOC,passCase == 0 ? 100 : 50,-0.25,-4.0,
                    sign * (passCase == 1 ? 0.96875 : 0.0));
      cache.update(key,&root.node,8,true,true);
      auto current = cache.find(key);
      testAssert(current != nullptr);
      checkAggregate(*current,passCase < 2 ? high.node : root.node);
      testAssert(current->firstExploreEvals.size() == 2);
      testAssert(current->firstExploreEvals.at(moveA).cacheWeight == (passCase == 0 ? 700.0f : 800.0f));
      testAssert(current->firstExploreEvals.at(Board::PASS_LOC).cacheWeight == (passCase == 0 ? 100.0f : 50.0f));
      checkSnapshot(*original,snapshot);
    }
  }
}

class UpdateStartGate {
  std::mutex mutex;
  std::condition_variable condition;
  int ready = 0;
  bool started = false;
public:
  void waitToStart() {
    std::unique_lock<std::mutex> lock(mutex);
    ready++;
    condition.notify_all();
    testAssert(condition.wait_for(lock,std::chrono::seconds(10),[&]() { return started; }));
  }

  void startWhenReady() {
    std::unique_lock<std::mutex> lock(mutex);
    testAssert(condition.wait_for(lock,std::chrono::seconds(10),[&]() { return ready == 2; }));
    started = true;
    condition.notify_all();
  }
};

void runConcurrentCacheTests() {
  enum UpdateMode { AGGREGATES, HINTS_ONLY, MIXED };
  const Loc moveA = Location::getLoc(3,3,9);
  const Loc moveB = Location::getLoc(5,5,9);
  EvalCacheTable cache(4);
  for(UpdateMode mode: {AGGREGATES,HINTS_ONLY,MIXED}) {
    for(int round = 0; round < 128; round++) {
      const Hash128 key((uint64_t)(3 * round + mode),(uint64_t)101);
      CacheTestNode seed(P_WHITE,mode == AGGREGATES ? 64 : mode == HINTS_ONLY ? 1000 : 900,0.125,1.0);
      for(int i = 0; i < 8; i++)
        seed.addChild(Location::getLoc(i,0,9),8,0.125,1.0,0.125);
      cache.update(key,&seed.node,8,false,true);
      auto original = cache.find(key);
      testAssert(original != nullptr);
      const EvalCacheEntry snapshot = *original;

      CacheTestNode first(P_WHITE,mode == AGGREGATES ? 100 : mode == HINTS_ONLY ? 800 : 900,-0.5,-8.0);
      CacheTestNode second(P_WHITE,mode == HINTS_ONLY ? 900 : 1000,0.75,12.0);
      if(mode != AGGREGATES) {
        first.addChild(moveA,16,-0.25,-4.0,-0.25);
        second.addChild(moveB,32,0.5,8.0,0.5);
      }
      const bool firstRecordsAggregate = mode == AGGREGATES;
      const bool secondRecordsAggregate = mode != HINTS_ONLY;
      UpdateStartGate gate;
      std::thread firstWriter([&]() {
        gate.waitToStart();
        cache.update(key,&first.node,8,!firstRecordsAggregate,firstRecordsAggregate);
      });
      std::thread secondWriter([&]() {
        gate.waitToStart();
        cache.update(key,&second.node,8,!secondRecordsAggregate,secondRecordsAggregate);
      });
      gate.startWhenReady();
      firstWriter.join();
      secondWriter.join();

      // These results hold for either serial order, so scheduling can affect
      // whether the old race is exposed, but cannot fail a correct update.
      auto current = cache.find(key);
      testAssert(current != nullptr);
      checkAggregate(*current,mode == HINTS_ONLY ? seed.node : second.node);
      testAssert(current->firstExploreEvals.size() == (mode == AGGREGATES ? 8 : 10));
      for(const auto& hint: snapshot.firstExploreEvals)
        checkHint(current->firstExploreEvals.at(hint.first),hint.second);
      if(mode != AGGREGATES) {
        checkHint(current->firstExploreEvals.at(moveA),FirstExploreEval(-0.25f,-4.0f,16.0f));
        checkHint(current->firstExploreEvals.at(moveB),FirstExploreEval(0.5f,8.0f,32.0f));
      }
      checkSnapshot(*original,snapshot);
    }
  }
}

} // namespace

void Tests::runEvalCacheTests() {
  std::cout << "Running eval cache tests" << std::endl;
  runSequentialCacheTests();
  runConcurrentCacheTests();
}
