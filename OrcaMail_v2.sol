// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title OrcaMail v2
 * @notice Wallet-to-wallet encrypted messaging on Lightchain.
 *
 *   Pricing model:
 *     • Opt-in / receiving — always free (just gas)
 *     • Sending            — first 5 messages free per wallet
 *     • After free tier    — $0.50 / month subscription (owner sets LCAI floor)
 *
 *   The frontend calculates the LCAI equivalent of $0.50 USD and sends that
 *   amount to subscribe(). The contract only enforces msg.value >= minSubPrice.
 *   Owner calls setMinSubPrice() when LCAI/USD rate shifts significantly.
 *   Example: LCAI = $0.01 → $0.50 = 50 LCAI → setMinSubPrice(50 ether)
 *
 * @dev Deployed on Lightchain L1 mainnet (chainId 9200).
 *      LCAI is the native coin — no ERC-20 approval needed.
 */
contract OrcaMail {

    // ─────────────────────────────────────────────
    //  Constants
    // ─────────────────────────────────────────────

    /// @notice Number of free sends each wallet gets before subscribing.
    uint8 public constant FREE_SENDS = 5;

    /// @notice Duration of one subscription payment (30 days).
    uint256 public constant SUB_DURATION = 30 days;

    // ─────────────────────────────────────────────
    //  State
    // ─────────────────────────────────────────────

    address public owner;

    /// @notice Minimum LCAI required to subscribe for 30 days.
    ///         Owner updates this when LCAI/USD rate shifts significantly.
    ///         Default: 100 LCAI safety floor.
    uint256 public minSubPrice;

    // Preference bitmask:
    //   bit 0 (1) = personal messages
    //   bit 1 (2) = announcements
    //   bit 2 (4) = ads
    struct MailPrefs {
        bool  optedIn;
        uint8 preferences;
    }

    mapping(address => MailPrefs) private _prefs;
    mapping(address => uint8)    public  freeSendsUsed;
    mapping(address => uint256)  public  subscriptionExpiry;

    // ─────────────────────────────────────────────
    //  Events
    // ─────────────────────────────────────────────

    event MailSent(
        address indexed from,
        address indexed to,
        uint8           msgType,
        bytes           encryptedBody,
        uint256         timestamp
    );
    event OptedIn(address indexed wallet, uint8 preferences);
    event OptedOut(address indexed wallet);
    event Subscribed(address indexed wallet, uint256 expiresAt, uint256 amountPaid);
    event MinSubPriceUpdated(uint256 oldPrice, uint256 newPrice);
    event FeesWithdrawn(address indexed to, uint256 amount);
    event AccessGranted(address indexed wallet, uint256 expiresAt);

    // ─────────────────────────────────────────────
    //  Modifiers
    // ─────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "OrcaMail: not owner");
        _;
    }

    // ─────────────────────────────────────────────
    //  Constructor
    // ─────────────────────────────────────────────

    /// @param _minSubPrice Minimum LCAI to subscribe (in wei). Use 100 ether = 100 LCAI.
    constructor(uint256 _minSubPrice) {
        owner       = msg.sender;
        minSubPrice = _minSubPrice;
    }

    // ─────────────────────────────────────────────
    //  Opt-in / Opt-out
    // ─────────────────────────────────────────────

    /**
     * @notice Register to receive OrcaMail messages (free, just gas).
     * @param preferences Bitmask: 1=personal, 2=announcements, 4=ads
     */
    function optIn(uint8 preferences) external {
        _prefs[msg.sender] = MailPrefs({ optedIn: true, preferences: preferences });
        emit OptedIn(msg.sender, preferences);
    }

    /**
     * @notice Update message-type preferences without opting out.
     * @param preferences New bitmask value.
     */
    function updatePreferences(uint8 preferences) external {
        require(_prefs[msg.sender].optedIn, "OrcaMail: not opted in");
        _prefs[msg.sender].preferences = preferences;
        emit OptedIn(msg.sender, preferences);
    }

    /**
     * @notice Remove yourself from the registry.
     */
    function optOut() external {
        delete _prefs[msg.sender];
        emit OptedOut(msg.sender);
    }

    // ─────────────────────────────────────────────
    //  Subscription
    // ─────────────────────────────────────────────

    /**
     * @notice Pay LCAI to unlock unlimited sending for 30 days.
     *
     *         The frontend calculates the LCAI equivalent of $0.50 USD using
     *         the live LCAI price and sends that amount. The contract verifies
     *         the amount meets the minimum floor.
     *
     *         Paying while active extends by 30 more days.
     *         Paying after expiry starts fresh from now.
     */
    function subscribe() external payable {
        require(msg.value >= minSubPrice, "OrcaMail: insufficient LCAI - check current price");

        uint256 base = (subscriptionExpiry[msg.sender] > block.timestamp)
            ? subscriptionExpiry[msg.sender]
            : block.timestamp;

        uint256 newExpiry = base + SUB_DURATION;
        subscriptionExpiry[msg.sender] = newExpiry;

        emit Subscribed(msg.sender, newExpiry, msg.value);
    }

    /**
     * @notice Check if a wallet has an active subscription.
     */
    function isSubscribed(address wallet) public view returns (bool) {
        return subscriptionExpiry[wallet] > block.timestamp;
    }

    // ─────────────────────────────────────────────
    //  Messaging
    // ─────────────────────────────────────────────

    /**
     * @notice Send an encrypted message to a single opted-in wallet.
     *
     *         Access rules:
     *           1. First FREE_SENDS (5) sends per wallet are free.
     *           2. After free tier: caller must have an active subscription.
     *
     * @param to            Recipient address (must have opted in).
     * @param msgType       0=personal, 1=announcement, 2=ad.
     * @param encryptedBody ECIES-encrypted message payload (hex bytes).
     */
    function sendMail(
        address to,
        uint8   msgType,
        bytes calldata encryptedBody
    ) external {
        require(_prefs[to].optedIn, "OrcaMail: recipient not opted in");

        if (freeSendsUsed[msg.sender] < FREE_SENDS) {
            freeSendsUsed[msg.sender] += 1;
        } else {
            require(isSubscribed(msg.sender), "OrcaMail: subscribe to continue sending");
        }

        emit MailSent(msg.sender, to, msgType, encryptedBody, block.timestamp);
    }

    /**
     * @notice Bulk-send a message to multiple recipients (owner only).
     * @param recipients  Array of opted-in addresses.
     * @param msgType     Message type byte.
     * @param encryptedBody Shared encrypted payload.
     */
    function bulkSendMail(
        address[] calldata recipients,
        uint8   msgType,
        bytes calldata encryptedBody
    ) external onlyOwner {
        uint256 ts = block.timestamp;
        for (uint256 i = 0; i < recipients.length; i++) {
            if (_prefs[recipients[i]].optedIn) {
                emit MailSent(msg.sender, recipients[i], msgType, encryptedBody, ts);
            }
        }
    }

    // ─────────────────────────────────────────────
    //  Owner controls
    // ─────────────────────────────────────────────

    /**
     * @notice Update the minimum LCAI subscription price.
     *         Call this when LCAI/USD rate shifts significantly.
     *         Example: LCAI = $0.01 → $0.50 = 50 LCAI → setMinSubPrice(50 ether)
     * @param _minSubPrice New minimum in wei (1 LCAI = 1 ether = 10^18 wei).
     */
    function setMinSubPrice(uint256 _minSubPrice) external onlyOwner {
        require(_minSubPrice > 0, "OrcaMail: price must be > 0");
        emit MinSubPriceUpdated(minSubPrice, _minSubPrice);
        minSubPrice = _minSubPrice;
    }

    /**
     * @notice Grant free subscription access to a wallet (e.g. for beta users).
     * @param wallet          Address to grant access to.
     * @param durationSeconds How long to grant (e.g. 30 days = 2592000).
     */
    function grantAccess(address wallet, uint256 durationSeconds) external onlyOwner {
        require(wallet != address(0), "OrcaMail: zero address");
        uint256 base = (subscriptionExpiry[wallet] > block.timestamp)
            ? subscriptionExpiry[wallet]
            : block.timestamp;
        subscriptionExpiry[wallet] = base + durationSeconds;
        emit AccessGranted(wallet, subscriptionExpiry[wallet]);
    }

    function withdrawFees() external onlyOwner {
        uint256 balance = address(this).balance;
        require(balance > 0, "OrcaMail: nothing to withdraw");
        (bool ok, ) = payable(owner).call{value: balance}("");
        require(ok, "OrcaMail: transfer failed");
        emit FeesWithdrawn(owner, balance);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "OrcaMail: zero address");
        owner = newOwner;
    }

    // ─────────────────────────────────────────────
    //  View helpers
    // ─────────────────────────────────────────────

    function hasOptedIn(address wallet) external view returns (bool) {
        return _prefs[wallet].optedIn;
    }

    function getPreferences(address wallet) external view returns (uint8) {
        return _prefs[wallet].preferences;
    }

    /// @notice Returns how many free sends remain for a wallet (0–5).
    function freeSendsRemaining(address wallet) external view returns (uint8) {
        uint8 used = freeSendsUsed[wallet];
        return used >= FREE_SENDS ? 0 : FREE_SENDS - used;
    }

    /// @notice Returns the UNIX timestamp when a wallet's subscription expires (0 = never subscribed).
    function getSubscriptionExpiry(address wallet) external view returns (uint256) {
        return subscriptionExpiry[wallet];
    }

    function getBalance() external view returns (uint256) {
        return address(this).balance;
    }

    // Allow contract to receive LCAI
    receive() external payable {}
}
